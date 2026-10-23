# testkit: exempt the check runner's subject, copied into synthetic suites
import unittest


class Example(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(2 + 2, 4)
