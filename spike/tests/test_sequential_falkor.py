import asyncio,unittest
from unittest.mock import patch
from src.sequential_falkor import SequentialFalkorDriver
class SequentialDriverTests(unittest.TestCase):
 def test_constructor_never_schedules_index_task(self):
  with patch('graphiti_core.driver.falkordb_driver.FalkorDB') as client, patch('asyncio.get_running_loop') as loop:
   SequentialFalkorDriver(database='spike_test');loop.return_value.create_task.assert_not_called();client.assert_called_once()
 def test_clone_is_fail_fast(self):
  with patch('graphiti_core.driver.falkordb_driver.FalkorDB'):
   driver=SequentialFalkorDriver(database='spike_build_a');self.assertRaisesRegex(RuntimeError,'unexpected_graphiti_driver_clone',driver.clone,'ai-pos')
