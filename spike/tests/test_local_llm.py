import unittest
from src.local_llm import clean_json_content,LocalStructuredOutputError
class LocalJsonTests(unittest.TestCase):
 def test_raw_json(self): self.assertEqual(clean_json_content('{"status":"ok"}'),'{"status":"ok"}')
 def test_fenced_json(self): self.assertEqual(clean_json_content('```json\n{"status":"ok"}\n```'),'{"status":"ok"}')
 def test_invalid_json(self):
  with self.assertRaises(LocalStructuredOutputError): clean_json_content('not json')
 def test_empty(self):
  with self.assertRaises(LocalStructuredOutputError): clean_json_content('  ')
