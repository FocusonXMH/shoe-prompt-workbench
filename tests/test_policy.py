import unittest
from policy import Forbidden, WorkbenchUser, can_transition_feedback, feedback_author_name, require_admin


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.admin = WorkbenchUser('admin', True)
        self.owner = WorkbenchUser('owner')
        self.other = WorkbenchUser('other')

    def test_admin_permission_cannot_be_derived_from_name(self):
        with self.assertRaises(Forbidden):
            require_admin(WorkbenchUser('admin'))
        require_admin(self.admin)

    def test_admin_can_resolve_and_author_can_reopen(self):
        self.assertTrue(can_transition_feedback(self.admin, 'owner', 'processing', 'awaiting_confirmation'))
        self.assertTrue(can_transition_feedback(self.admin, 'owner', 'processing', 'resolved'))
        self.assertTrue(can_transition_feedback(self.owner, 'owner', 'resolved', 'processing'))
        self.assertTrue(can_transition_feedback(self.other, 'owner', 'resolved', 'processing'))
        self.assertTrue(can_transition_feedback(self.owner, 'owner', 'awaiting_confirmation', 'resolved'))
        self.assertFalse(can_transition_feedback(self.other, 'owner', 'awaiting_confirmation', 'resolved'))

    def test_anonymity_is_visible_only_to_admin_and_author(self):
        self.assertEqual(feedback_author_name(self.other, 'owner', '姓名', True), '匿名用户')
        self.assertEqual(feedback_author_name(self.admin, 'owner', '姓名', True), '姓名')
        self.assertEqual(feedback_author_name(self.owner, 'owner', '姓名', True), '匿名用户')


if __name__ == '__main__':
    unittest.main()
