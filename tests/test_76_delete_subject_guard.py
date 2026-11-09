"""批量删除绑定学科，不能用旧标签页的选择删除新学科同编号对象。"""
import unittest
from unittest import mock
from tools import dashboard


class DeleteSubjectGuard(unittest.TestCase):
    def test_old_tab_subject_is_rejected(self):
        with mock.patch.object(dashboard.subjects_mod, 'current', return_value='subject-b'):
            self.assertIn('学科已切换', dashboard._deletion_subject_error(
                {'expected_subject': 'subject-a'})['error'])
            self.assertIsNone(dashboard._deletion_subject_error({'expected_subject': 'subject-b'}))
            self.assertIsNone(dashboard._deletion_subject_error({}))  # 旧脚本兼容
            self.assertIsNotNone(dashboard._deletion_subject_error({'expected_subject': ''}))


if __name__ == '__main__':
    unittest.main()
