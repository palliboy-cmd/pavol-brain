import unittest
from pathlib import Path
from src.artifact_validation import parse,validate
class ArtifactTests(unittest.TestCase):
 def test_parse(self): self.assertEqual(parse('repo://ai-pos/README.md'),('ai-pos','README.md'))
 def test_invalid(self): self.assertFalse(validate('repo://ai-pos/not-a-file',Path(__file__).parents[1]/'repos.yaml')['valid'])
