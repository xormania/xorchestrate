"""Optional bundle publication reports success only from a verified destination receipt."""

import json

LOST_RECEIPT_FIELDS = ({"verified": False}, {"verified": "true"}, {"bundle_id": "0" * 64})


class BundlePublicationContract:
    def publish(self, publisher, *, bundle=None, lineage=None, attempt_id="attempt-one"):
        return self.snapshot.publish_bundle(self.bundle if bundle is None else bundle, publisher,
                                            expected_lineage=self.lineage if lineage is None else lineage,
                                            attempt_id=attempt_id)

    def assert_not_published(self, receipt):
        self.assertEqual(receipt["status"], "unavailable")
        self.assertNotIn("destination", receipt)
        self.assertNotIn("remote_receipt", receipt)
        self.assertEqual(receipt["bundle_id"], self.manifest["bundle_id"])
        self.assertEqual(receipt["lineage"], self.lineage)

    def test_absent_remote_is_published_once_and_reported_from_its_verified_lookup(self):
        identity = self.manifest["bundle_id"]
        self.assertEqual({key: self.manifest["source"][key] for key in self.lineage}, self.lineage)
        publisher = self.publisher()
        receipt = self.publish(publisher)
        self.assertEqual(receipt["status"], "verified")
        self.assertEqual(receipt["bundle_id"], identity)
        self.assertEqual(receipt["attempt_id"], "attempt-one")
        self.assertEqual(receipt["lineage"], self.lineage)
        self.assertEqual(receipt["destination"], publisher.destination(identity))
        self.assertIs(receipt["remote_receipt"]["verified"], True)
        self.assertEqual(publisher.publications, [{"bundle": self.bundle, "expected_lineage": self.lineage}])
        self.assertEqual([lookup["bundle_id"] for lookup in publisher.lookups], [identity, identity],
                         "the destination is looked up before transmission and verified after it")
        self.assertEqual(self.snapshot.verify_bundle(self.bundle), self.manifest)

    def test_retry_after_lost_acknowledgment_reports_verified_remote_without_retransmitting(self):
        publisher = self.publisher(lose_acknowledgment=True)
        lost = self.publish(publisher, attempt_id="attempt-one")
        self.assert_not_published(lost)
        self.assertEqual(lost["reason"], "publication_unavailable")
        self.assertEqual(len(publisher.publications), 1)
        retry = self.publish(publisher, attempt_id="attempt-two")
        self.assertEqual(retry["status"], "verified")
        self.assertEqual(retry["attempt_id"], "attempt-two")
        self.assertEqual(retry["destination"], publisher.destination(self.manifest["bundle_id"]))
        self.assertEqual(len(publisher.publications), 1, "a verified remote bundle must not be transmitted again")

    def test_unverified_post_publication_lookup_never_becomes_success(self):
        wrong_lineage = {"lineage": {**self.lineage, "dataset_id": "other-dataset"}}
        for changes in (*LOST_RECEIPT_FIELDS, wrong_lineage):
            with self.subTest(changes=changes):
                publisher = self.publisher(receipt_changes=changes)
                receipt = self.publish(publisher)
                self.assertEqual(len(publisher.publications), 1, "an absent remote is transmitted once")
                self.assertNotEqual(receipt["status"], "verified")
                self.assert_not_published(receipt)

    def test_destination_failure_is_unavailable_without_exposing_its_error(self):
        for operation in ("lookup", "publish"):
            with self.subTest(operation=operation):
                receipt = self.publish(self.publisher(fail=operation))
                self.assert_not_published(receipt)
                self.assertEqual(receipt["reason"], "publication_unavailable")
                self.assertNotIn("private destination exception text", json.dumps(receipt))

    def test_lineage_mismatch_is_refused_before_contacting_the_destination(self):
        variants = ({**self.lineage, "dataset_id": "other-dataset"},
                    {**self.lineage, "collector_id": "other-collector"},
                    {"dataset_id": self.lineage["dataset_id"]})
        for lineage in variants:
            with self.subTest(lineage=lineage):
                publisher = self.publisher()
                with self.assertRaises(self.snapshot.SnapshotError):
                    self.publish(publisher, lineage=lineage)
                self.assertEqual(publisher.contacts(), 0)

    def test_unverifiable_bundle_is_refused_before_contacting_the_destination(self):
        for member in ("database", "manifest"):
            with self.subTest(member=member):
                bundle, _ = self.create_bundle(f"tampered-{member}")
                self.tamper(bundle, member)
                with self.assertRaises(self.snapshot.SnapshotError) as direct:
                    self.snapshot.verify_bundle(bundle)
                publisher = self.publisher()
                with self.assertRaises(self.snapshot.SnapshotError) as published:
                    self.publish(publisher, bundle=bundle)
                self.assertEqual(str(published.exception), str(direct.exception),
                                 "publication must refuse with the bundle verification result")
                self.assertEqual(publisher.contacts(), 0)
