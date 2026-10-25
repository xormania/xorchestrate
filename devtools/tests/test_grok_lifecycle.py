import asyncio
import unittest

from fixtures import native_boundaries as boundaries


class PromptRPC:
    """Hold responses so lifecycle tests choose protocol order without sleeps."""

    def __init__(self):
        self.calls = []
        self.prompts = asyncio.Queue()

    async def request(self, method, params, *, timeout=120, on_sent=None):
        self.calls.append((method, params))
        if method != "session/prompt":
            return {}
        future = asyncio.get_running_loop().create_future()
        await on_sent()
        self.prompts.put_nowait((params, future))
        return await future


class GrokLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        boundaries.NativeBoundaryFixture.setUp(self)
        self.native = boundaries.NativeBoundaryFixture.native(self, "grok")
        self.native.state("idle", native_id=self.native.session)
        self.native.rpc = PromptRPC()
        self.messages = 0

    async def asyncTearDown(self):
        for task in list(self.native.tasks):
            task.cancel()
        await asyncio.gather(*self.native.tasks, return_exceptions=True)

    def message(self):
        self.messages += 1
        queued = self.store.send("owner", "worker", "Direction " + str(self.messages), key=str(self.messages))
        message = self.store.take_delivery("worker", scoped=True, generation=self.generation)
        self.assertEqual(queued["id"], message["id"])
        return message

    async def prompt(self):
        await self.native.deliver(self.message())
        params, response = await asyncio.wait_for(self.native.rpc.prompts.get(), 1)
        task = next(iter(self.native.tasks))
        self.assertEqual("session/prompt", self.native.rpc.calls[-1][0])
        self.assertEqual(self.native.session, params["sessionId"])
        return params["_meta"]["promptId"], response, task

    async def update(self, kind, prompt_id=None):
        params = {"sessionId": self.native.session, "update": {"sessionUpdate": kind}}
        if prompt_id is not None:
            params["_meta"] = {"promptId": prompt_id}
        await self.native.notification("session/update", params)

    async def complete(self, prompt_id=None):
        params = {"sessionId": self.native.session}
        if prompt_id is not None:
            params["promptId"] = prompt_id
        await self.native.notification("_x.ai/session/prompt_complete", params)

    async def finish(self, response, task):
        response.set_result({"stopReason": "end_turn"})
        await task

    def assert_state(self, state):
        self.assertEqual(state, self.store.member("worker")["state"])
        self.assertEqual(state == "active", self.native.busy)

    async def test_standard_prompt_response_idles_and_next_message_starts_fresh_turn(self):
        first_id, response, task = await self.prompt()
        await self.update("agent_thought_chunk", first_id)
        self.assert_state("active")
        await self.finish(response, task)
        self.assertFalse(self.native.native_busy)
        self.assertEqual(0, self.native.prompts_in_flight)
        self.assert_state("idle")

        next_id, response, task = await self.prompt()
        self.assertNotEqual(first_id, next_id)
        self.assertEqual(["session/prompt", "session/prompt"], [call[0] for call in self.native.rpc.calls])
        await self.finish(response, task)
        self.assert_state("idle")

    async def test_unacknowledged_interjection_survives_original_prompt_completion(self):
        _, response, task = await self.prompt()
        message = self.message()
        await self.native.deliver(message)
        self.assertEqual("_x.ai/interject", self.native.rpc.calls[-1][0])
        await self.finish(response, task)
        self.assert_state("active")
        self.assertEqual({message["id"]}, self.native.pending_interjections)
        self.store.acknowledge("worker", message["id"])
        self.native.grok_activity()
        self.assert_state("idle")

    async def test_old_response_and_completion_cannot_idle_acknowledged_interjected_turn(self):
        first_id, response, task = await self.prompt()
        message = self.message()
        await self.native.deliver(message)
        self.store.acknowledge("worker", message["id"])
        await self.update("agent_thought_chunk", "interjected-turn")
        await self.finish(response, task)
        self.assertEqual(set(), self.native.pending_interjections)
        self.assertEqual(0, self.native.prompts_in_flight)
        self.assert_state("active")
        await self.complete(first_id)
        await self.update("tool_call_update", first_id)
        self.assertTrue(self.native.native_busy)
        self.assert_state("active")
        await self.complete("interjected-turn")
        self.assert_state("idle")

    async def test_new_anonymous_activity_after_completion_survives_old_response(self):
        first_id, response, task = await self.prompt()
        message = self.message()
        await self.native.deliver(message)
        self.store.acknowledge("worker", message["id"])
        await self.complete(first_id)
        await self.update("agent_thought_chunk")
        await self.finish(response, task)
        self.assertTrue(self.native.native_busy)
        self.assert_state("active")
        await self.complete()
        self.assert_state("idle")

    async def test_late_older_chunks_before_response_cannot_replace_newer_turn(self):
        for response_first in (True, False):
            with self.subTest(response_first=response_first):
                first_id, response, task = await self.prompt()
                message = self.message()
                await self.native.deliver(message)
                self.store.acknowledge("worker", message["id"])
                interjected_id = "interjected-" + first_id
                await self.update("agent_thought_chunk", interjected_id)
                await self.update("tool_call_update", first_id)
                self.assertEqual(interjected_id, self.native.grok_prompt_id)
                if response_first:
                    await self.finish(response, task)
                    self.assert_state("active")
                    await self.complete(interjected_id)
                else:
                    await self.complete(interjected_id)
                    self.assert_state("active")
                    self.assertEqual(1, self.native.prompts_in_flight)
                    await self.finish(response, task)
                self.assert_state("idle")

    async def test_completion_before_response_does_not_reactivate_from_late_same_turn_chunks(self):
        prompt_id, response, task = await self.prompt()
        await self.update("turn_completed", prompt_id)
        self.assertFalse(self.native.native_busy)
        self.assert_state("active")  # The response is still outstanding.
        await self.update("tool_call_update", prompt_id)
        self.assertFalse(self.native.native_busy)
        await self.finish(response, task)
        self.assert_state("idle")
        await self.complete(prompt_id)
        self.assert_state("idle")

    async def test_anonymous_completion_remembers_the_current_prompt_identity(self):
        prompt_id, response, task = await self.prompt()
        await self.complete()
        await self.update("tool_call_update", prompt_id)
        self.assertFalse(self.native.native_busy)
        await self.finish(response, task)
        self.assert_state("idle")

    async def test_submitted_prompt_error_does_not_claim_completion_or_replay(self):
        _, response, task = await self.prompt()
        response.set_exception(TimeoutError("Response not observed"))
        await task
        self.assert_state("active")
        self.assertEqual(0, self.native.prompts_in_flight)
        self.assertIsNone(self.store.take_delivery("worker", scoped=True, generation=self.generation))
        self.assertEqual(1, len(self.native.rpc.calls))
        errors = self.store.events(actor="worker", kind="native_prompt_error")["events"]
        self.assertIn("Response not observed", errors[-1]["data"]["error"])

    async def test_malformed_prompt_response_does_not_claim_completion(self):
        _, response, task = await self.prompt()
        response.set_result({})
        await task
        self.assert_state("active")
        errors = self.store.events(actor="worker", kind="native_prompt_error")["events"]
        self.assertIn("stopReason", errors[-1]["data"]["error"])
