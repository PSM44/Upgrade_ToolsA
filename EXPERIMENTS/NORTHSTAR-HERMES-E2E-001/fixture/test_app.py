import unittest
from app import is_palindrome

class TestIsPalindrome(unittest.TestCase):
    def test_palindrome_with_punctuation(self):
        self.assertTrue(is_palindrome("A man, a plan, a canal: Panama!"))
    
    def test_palindrome_with_apostrophe(self):
        self.assertTrue(is_palindrome("No 'x' in Nixon"))
    
    def test_non_palindrome(self):
        self.assertFalse(is_palindrome("OpenAI"))
    
    def test_only_punctuation(self):
        self.assertTrue(is_palindrome("!!!"))
    
    def test_case_insensitive(self):
        self.assertTrue(is_palindrome("RaceCar"))
    
    def test_empty_string(self):
        self.assertTrue(is_palindrome(""))
    
    def test_single_character(self):
        self.assertTrue(is_palindrome("a"))

if __name__ == "__main__":
    unittest.main()
