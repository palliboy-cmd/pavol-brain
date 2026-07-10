import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from evaluate import percentile
class EvaluationTests(unittest.TestCase):
 def test_percentile(self): self.assertEqual(percentile([1,2,3,4,5],.5),3);self.assertEqual(percentile([], .95),None)
