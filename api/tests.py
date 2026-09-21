from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import Answer, Category, Question, Quiz, QuizCategory


class BcsTargetTopicImportViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_user = User.objects.create_user(
            username='importer',
            password='password',
            is_staff=True,
        )
        self.token = Token.objects.create(user=self.staff_user)
        self.parent_category = Category.objects.create(name='Question Bank')
        self.url = '/api/import/bcstarget-topic/'

    def test_import_requires_staff_token(self):
        response = self.client.post(self.url, {}, format='json')

        self.assertIn(response.status_code, [401, 403])

    def test_import_creates_topic_questions_and_answers(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        response = self.client.post(self.url, self._payload(), format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['created'], 2)
        self.assertEqual(response.data['skipped'], 0)
        self.assertTrue(response.data['topic_created'])

        topic = Category.objects.get(name='বাংলা ভাষা', parent=self.parent_category)
        self.assertEqual(topic.level, self.parent_category.level + 1)
        self.assertEqual(Question.objects.filter(category=topic).count(), 2)
        self.assertEqual(Answer.objects.filter(question__category=topic).count(), 8)

        first_question = Question.objects.get(question_text='বাংলা ভাষার আদি রূপ কোনটি?')
        correct_answer = first_question.answers.get(is_correct=True)
        self.assertEqual(correct_answer.text, 'প্রাকৃত')

    def test_import_skips_duplicate_questions_in_same_topic(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        first_response = self.client.post(self.url, self._payload(), format='json')
        second_response = self.client.post(self.url, self._payload(), format='json')

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.data['created'], 0)
        self.assertEqual(second_response.data['skipped'], 2)
        self.assertFalse(second_response.data['topic_created'])

        topic = Category.objects.get(name='বাংলা ভাষা', parent=self.parent_category)
        self.assertEqual(Question.objects.filter(category=topic).count(), 2)

    def _payload(self):
        return {
            'parent_category_id': self.parent_category.id,
            'topic_name': 'বাংলা ভাষা',
            'topic_url': 'https://bcstarget.com/topics/28/questions',
            'questions': [
                {
                    'source_question_id': '1',
                    'source_question_url': 'https://bcstarget.com/questions/1',
                    'question_text': 'বাংলা ভাষার আদি রূপ কোনটি?',
                    'option_a': 'পালি',
                    'option_b': 'প্রাকৃত',
                    'option_c': 'সংস্কৃত',
                    'option_d': 'অপভ্রংশ',
                    'correct_option': 'B',
                    'explanation': 'বাংলা ভাষার উৎস প্রাকৃত।',
                },
                {
                    'source_question_id': '2',
                    'source_question_url': 'https://bcstarget.com/questions/2',
                    'question_text': 'বাংলা কোন ভাষা পরিবারভুক্ত?',
                    'option_a': 'ইন্দো-ইউরোপীয়',
                    'option_b': 'দ্রাবিড়',
                    'option_c': 'অস্ট্রিক',
                    'option_d': 'চীনা-তিব্বতি',
                    'correct_option': 'A',
                    'explanation': '',
                },
            ],
        }


class SiteRouteTests(TestCase):
    def test_robots_txt_is_served(self):
        response = self.client.get('/robots.txt')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertIn('User-agent: *', response.content.decode('utf-8'))


class BcsTargetAnswersheetImportViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_user = User.objects.create_user(
            username='quiz-importer',
            password='password',
            is_staff=True,
        )
        self.token = Token.objects.create(user=self.staff_user)
        self.quiz_category = QuizCategory.objects.create(name='BCS Model Tests')
        self.url = '/api/import/bcstarget-answersheet/'

    def test_import_requires_staff_token(self):
        response = self.client.post(self.url, {}, format='json')

        self.assertIn(response.status_code, [401, 403])

    def test_import_creates_question_bank_quiz_with_duration(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        response = self.client.post(self.url, self._payload(), format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['created'], 2)
        self.assertEqual(response.data['skipped'], 0)
        self.assertTrue(response.data['quiz_created'])

        quiz = Quiz.objects.get(name='৫০ তম বিসিএস প্রিলিমিনারি টেস্ট')
        self.assertEqual(quiz.category, self.quiz_category)
        self.assertEqual(quiz.quiz_type, Quiz.QuizType.QUESTION_BANK)
        self.assertEqual(quiz.duration_minutes, 2)
        self.assertEqual(quiz.questions.count(), 2)
        self.assertEqual(Answer.objects.filter(question__quizzes=quiz).count(), 8)

        first_question = quiz.questions.get(question_text='জারিনের জন্ম ২৯ ফেব্রুয়ারী।')
        self.assertEqual(first_question.answers.get(is_correct=True).text, '2004')

    def test_import_skips_duplicate_questions_attached_to_quiz(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        first_response = self.client.post(self.url, self._payload(), format='json')
        second_response = self.client.post(self.url, self._payload(), format='json')

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.data['created'], 0)
        self.assertEqual(second_response.data['skipped'], 2)
        self.assertFalse(second_response.data['quiz_created'])

        quiz = Quiz.objects.get(name='৫০ তম বিসিএস প্রিলিমিনারি টেস্ট')
        self.assertEqual(quiz.questions.count(), 2)

    def _payload(self):
        return {
            'quiz_category_id': self.quiz_category.id,
            'quiz_name': '৫০ তম বিসিএস প্রিলিমিনারি টেস্ট',
            'answersheet_url': 'https://bcstarget.com/job-solution/categories/12/answersheet/2598',
            'duration_minutes': 2,
            'questions': [
                {
                    'source_question_id': '150786',
                    'source_question_url': 'https://bcstarget.com/questions/150786',
                    'question_text': 'জারিনের জন্ম ২৯ ফেব্রুয়ারী।',
                    'option_a': '2002',
                    'option_b': '2004',
                    'option_c': '2006',
                    'option_d': '2010',
                    'correct_option': 'B',
                    'explanation': '২৯ ফেব্রুয়ারি শুধু লিপ ইয়ারেই আসে।',
                },
                {
                    'source_question_id': '150787',
                    'source_question_url': 'https://bcstarget.com/questions/150787',
                    'question_text': "পারমাণবিক চুল্লিতে 'মডারেটরের' প্রাথমিক কাজ হলো:",
                    'option_a': 'অতিরিক্ত নিউট্রন শোষণ',
                    'option_b': 'তাপ স্থানান্তর',
                    'option_c': 'নিউট্রন ধীর করা',
                    'option_d': 'গামা বিকরণ',
                    'correct_option': 'C',
                    'explanation': 'মডারেটরের প্রধান কাজ নিউট্রন ধীর করা।',
                },
            ],
        }
