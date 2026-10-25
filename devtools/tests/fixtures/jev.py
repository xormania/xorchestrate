"""Offline Jev transport primitives; no repository, state, or workspace setup."""

import json
import os
from unittest.mock import MagicMock, patch


class JevTransportFixture:

    def setUp(self):
        self.questions = {"q": {"type": "noul", "instructions": "Is it relevant?"}}
        self.key = patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-secret-never-log"})
        self.key.start()
        self.addCleanup(self.key.stop)
        self.transport()

    def transport(self):
        """Install the offline wire boundary independently of a credential source."""
        self.opener = MagicMock()
        self.factory = patch("devtools.jev.urllib.request.build_opener", return_value=self.opener)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def response(self, value):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = value if isinstance(value, bytes) else json.dumps(value).encode()
        return response
