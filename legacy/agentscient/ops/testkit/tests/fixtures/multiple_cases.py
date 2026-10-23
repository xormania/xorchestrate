# testkit: exempt the check runner's subject, copied into synthetic suites
import unittest


class First(unittest.TestCase):
    def test_a(self):
        print(self.id())
        self.assertEqual(2 + 2, 4)

    def test_b(self):
        print(self.id())
        self.assertTrue(True)


class Second(unittest.TestCase):
    def test_c(self):
        print(self.id())
        self.assertTrue(True)

    def test_d(self):
        print(self.id())
        self.assertTrue(True)
