"""
End-to-end check that the quiz import endpoint persists `topic`, using the exact
payload shape the browser extension sends (including a real Bengali topic).

Also covers the re-import case, which is what fixes already-imported quizzes:
the quiz is matched on (source_name, external_quiz_id) and its topic/name are
updated in place without duplicating questions.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Course, CourseQuiz


# Real topic text taken from the live archive list page for exam 13834.
LIVE_TOPIC = (
    'à¦ªà¦¾à¦°à§à¦Ÿ-à§§) à¦ªà§à¦°à¦¾à¦šà§€à¦¨ à¦¸à¦­à§à¦¯à¦¤à¦¾à¦¸à¦®à§‚à¦¹, à¦¸à¦¾à¦®à§à¦°à¦¾à¦œà§à¦¯à¦¸à¦®à§‚à¦¹, à¦—à§à¦°à§à¦¤à§à¦¬à¦ªà§‚à¦°à§à¦£ à¦˜à¦Ÿà¦¨à¦¾à¦¬à¦²à§€ (à¦¯à§à¦¦à§à¦§ à¦“ à¦¬à¦¿à¦ªà§à¦²à¦¬ à¦‡à¦¤à§à¦¯à¦¾à¦¦à¦¿), '
    'à¦§à¦°à§à¦®à¦¸à¦®à§‚à¦¹à§‡à¦° à¦‡à¦¤à¦¿à¦¹à¦¾à¦¸ à¦à¦¬à¦‚ à¦‡à¦¤à¦¿à¦¹à¦¾à¦¸à§‡à¦° à¦‰à¦²à§à¦²à§‡à¦–à¦¯à§‹à¦—à§à¦¯ à¦¬à§à¦¯à¦•à§à¦¤à¦¿à¦¬à¦°à§à¦— à¦ªà¦¾à¦°à§à¦Ÿ-à§¨) English Literature: Topics '
    '1. Literary Terms: With Genres 2. Literary Terms: (Mix) 3. Famous Quotation and Characters.'
)


def extension_payload(quiz_name, topic, external_quiz_id):
    """Mirrors the payload built by importBatchQuizItem in popup.js."""
    return {
        'quiz_name': quiz_name,
        'topic': topic,
        'quiz_type': CourseQuiz.QuizType.QUESTION_BANK,
        'duration_minutes': 30,
        'correct_mark': '1.00',
        'wrong_mark': '0.00',
        'unanswered_mark': '0.00',
        'source_name': 'livemcq',
        'external_quiz_id': external_quiz_id,
        'source_url': f'https://livemcq.com/archive-question-show/{external_quiz_id}/',
        'is_active': True,
        'questions': [
            {
                'source_question_id': f'{external_quiz_id}-1',
                'source_question_url': f'https://livemcq.com/archive-question-show/{external_quiz_id}/#1',
                'question_text': 'à¦¨à§‡à¦²à¦¸à¦¨ à¦®à§à¦¯à¦¾à¦¨à§à¦¡à§‡à¦²à¦¾à¦° à¦°à¦¾à¦œà¦¨à§ˆà¦¤à¦¿à¦• à¦¦à¦²à§‡à¦° à¦¨à¦¾à¦® à¦•à¦¿?',
                'option_a': 'à¦†à¦«à§à¦°à¦¿à¦•à¦¾à¦¨ à¦¸à§‹à¦¸à§à¦¯à¦¾à¦²à¦¿à¦¸à§à¦Ÿ à¦ªà¦¾à¦°à§à¦Ÿà¦¿',
                'option_b': 'à¦¨à§à¦¯à¦¾à¦¶à¦¨à¦¾à¦²à¦¿à¦¸à§à¦Ÿ à¦ªà¦¾à¦°à§à¦Ÿà¦¿',
                'option_c': 'à¦†à¦«à§à¦°à¦¿à¦•à¦¾à¦¨ à¦•à¦‚à¦—à§à¦°à§‡à¦¸',
                'option_d': 'à¦†à¦«à§à¦°à¦¿à¦•à¦¾à¦¨ à¦¨à§à¦¯à¦¾à¦¶à¦¨à¦¾à¦² à¦•à¦‚à¦—à§à¦°à§‡à¦¸',
                'correct_option': 'D',
                'explanation': '',
            },
            {
                'source_question_id': f'{external_quiz_id}-2',
                'source_question_url': f'https://livemcq.com/archive-question-show/{external_quiz_id}/#2',
                'question_text': 'Which is an epistolary novel?',
                'option_a': 'Pamela',
                'option_b': 'Tom Jones',
                'option_c': 'Hamlet',
                'option_d': 'Ulysses',
                'correct_option': 'A',
                'explanation': '',
            },
        ],
    }


class QuizTopicImportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_user = User.objects.create_user(
            username='staff', password='password', is_staff=True
        )
        token = Token.objects.create(user=self.staff_user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        course_response = self.client.post(
            '/api/courses/',
            {
                'name': 'Archive Course',
                'description': 'Archived content',
                'course_type': Course.CourseType.ARCHIVE,
                'is_published': True,
                'allow_self_enrollment': True,
            },
            format='json',
        )
        self.assertEqual(course_response.status_code, 201, course_response.data)
        self.course_id = course_response.data['id']
        self.url = f'/api/courses/{self.course_id}/quizzes/import/'

    def test_topic_is_persisted_on_first_import(self):
        response = self.client.post(
            self.url,
            extension_payload('Exam - 25', LIVE_TOPIC, '13834'),
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['quiz_created'])
        self.assertEqual(response.data['created'], 2)

        # Present in the API response...
        self.assertEqual(response.data['quiz']['topic'], LIVE_TOPIC)

        # ...and actually stored in the database.
        quiz = CourseQuiz.objects.get(course_id=self.course_id, external_quiz_id='13834')
        self.assertEqual(quiz.topic, LIVE_TOPIC)
        self.assertEqual(quiz.name, 'Exam - 25')

    def test_reimport_backfills_topic_and_name_without_duplicating_questions(self):
        """This is the path that repairs quizzes imported by the old extension."""
        # Simulate what the OLD extension produced: exam-id name, empty topic.
        first = self.client.post(
            self.url,
            extension_payload('Exam - 13834', '', '13834'),
            format='json',
        )
        self.assertEqual(first.status_code, 200, first.data)
        quiz_id = first.data['quiz']['id']

        stale = CourseQuiz.objects.get(pk=quiz_id)
        self.assertEqual(stale.topic, '')
        self.assertEqual(stale.name, 'Exam - 13834')
        self.assertEqual(stale.questions.count(), 2)

        # Re-import with the fixed extension output.
        second = self.client.post(
            self.url,
            extension_payload('Exam - 25', LIVE_TOPIC, '13834'),
            format='json',
        )
        self.assertEqual(second.status_code, 200, second.data)

        # Same quiz reused, no new questions created.
        self.assertFalse(second.data['quiz_created'])
        self.assertEqual(second.data['quiz']['id'], quiz_id)
        self.assertEqual(second.data['created'], 0)
        self.assertEqual(second.data['skipped'], 2)

        repaired = CourseQuiz.objects.get(pk=quiz_id)
        self.assertEqual(repaired.topic, LIVE_TOPIC)
        self.assertEqual(repaired.name, 'Exam - 25')
        self.assertEqual(repaired.questions.count(), 2)

    def test_omitting_topic_stores_blank_rather_than_failing(self):
        """DRF drops unknown/missing keys silently - this documents that."""
        payload = extension_payload('Exam - 25', '', '13835')
        del payload['topic']

        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        quiz = CourseQuiz.objects.get(course_id=self.course_id, external_quiz_id='13835')
        self.assertEqual(quiz.topic, '')

