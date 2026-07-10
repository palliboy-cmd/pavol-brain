import unittest
from src.policy import initial_state
class PolicyTests(unittest.TestCase):
 def test_inference_is_candidate(self): self.assertEqual(initial_state({'source_assertion':'agent_inference','payload':{}})[0],'candidate')
 def test_curated_is_accepted(self): self.assertEqual(initial_state({'source_assertion':'imported_curated','payload':{}})[0],'accepted')
