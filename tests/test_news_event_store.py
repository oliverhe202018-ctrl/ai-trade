import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.news_event_store import NewsEventStore

class TestNewsEventStore(unittest.TestCase):
    
    def setUp(self):
        self.fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.store = NewsEventStore(self.db_path)
        
    def tearDown(self):
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
        except:
            pass

    def test_scenario_c_duplicate_events(self):
        """场景 C：重复事件只入库一次"""
        event = {
            "event_id": "duplicate_hash_123",
            "source": "test",
            "event_type": "flash",
            "event_time": "2024-01-01 10:00:00",
            "title": "Test Title"
        }
        
        # 第一次写入成功
        res1 = self.store.save_event(event)
        self.assertTrue(res1)
        
        # 第二次写入返回 False，但不崩溃
        res2 = self.store.save_event(event)
        self.assertFalse(res2)
        
        # 第三次写入返回 False，但不崩溃
        res3 = self.store.save_event(event)
        self.assertFalse(res3)
        
        self.assertEqual(self.store.count_events(), 1)

    def test_scenario_d_bulk_save_with_duplicates(self):
        events = [
            {"event_id": "hash1", "title": "A"},
            {"event_id": "hash1", "title": "A"},
            {"event_id": "hash2", "title": "B"}
        ]
        count = self.store.save_events(events)
        self.assertEqual(count, 2)
        self.assertEqual(self.store.count_events(), 2)

if __name__ == "__main__":
    unittest.main()
