def is_palindrome(text):
    """Check if text is a palindrome (case-insensitive, ignores non-alphanumeric characters)."""
    cleaned = ''.join(c.lower() for c in text if c.isalnum())
    return cleaned == cleaned[::-1]
