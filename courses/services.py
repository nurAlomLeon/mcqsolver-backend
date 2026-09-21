import csv
import hashlib
import io
import re
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import (
    Course,
    CourseAnswer,
    CourseEnrollment,
    CourseQuestion,
    CourseQuiz,
    CourseQuizAttempt,
    CourseQuizSubmission,
)


User = get_user_model()

_BENGALI_DIGITS = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')


def to_bengali_number(number):
    return str(number).translate(_BENGALI_DIGITS)


def normalize_text(value):
    return re.sub(r'\s+', ' ', (value or '').strip())


def reorder_course_quizzes(course, quiz_id_order):
    """Assign positions 1..N in the supplied quiz id order."""
    quizzes = list(course.quizzes.all())
    quiz_by_id = {quiz.id: quiz for quiz in quizzes}
    ordered_ids = list(quiz_id_order)

    if set(ordered_ids) != set(quiz_by_id) or len(ordered_ids) != len(quiz_by_id):
        raise DjangoValidationError(
            'Exam order must include every exam in this course exactly once.'
        )

    with transaction.atomic():
        max_position = max((quiz.position for quiz in quizzes), default=0)
        temp_base = max_position + 1

        for index, quiz_id in enumerate(ordered_ids):
            quiz = quiz_by_id[quiz_id]
            quiz.position = temp_base + index
            quiz.save(update_fields=['position', 'updated_at'])

        for index, quiz_id in enumerate(ordered_ids, start=1):
            quiz = quiz_by_id[quiz_id]
            quiz.position = index
            quiz.save(update_fields=['position', 'updated_at'])

    return list(course.quizzes.order_by('position', 'id'))


def auto_reorganize_course_quizzes(course):
    """Renumber every exam 1..N and rename it to Bengali exam numbering."""
    quizzes = list(course.quizzes.order_by('position', 'id'))
    if not quizzes:
        return []

    quiz_ids = [quiz.id for quiz in quizzes]
    reorder_course_quizzes(course, quiz_ids)

    for index, quiz_id in enumerate(quiz_ids, start=1):
        quiz = CourseQuiz.objects.get(pk=quiz_id)
        quiz.name = f'পরীক্ষা - {to_bengali_number(index)}'
        quiz.save(update_fields=['name', 'updated_at'])

    return list(course.quizzes.order_by('position', 'id'))


def compute_question_fingerprint(question_payload):
    parts = [normalize_text(question_payload['question_text'])]
    for answer in question_payload['answers']:
        parts.append(normalize_text(answer['text']))
        parts.append('1' if answer['is_correct'] else '0')
    parts.append(normalize_text(question_payload.get('explanation', '')))
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def assign_positions(question_payloads):
    assigned_questions = []
    for question_index, question in enumerate(question_payloads, start=1):
        assigned_answers = []
        for answer_index, answer in enumerate(question['answers'], start=1):
            assigned_answers.append({
                'position': answer.get('position') or answer_index,
                'text': answer['text'],
                'is_correct': answer['is_correct'],
            })
        assigned_question = {
            'position': question.get('position') or question_index,
            'question_text': question['question_text'],
            'explanation': question.get('explanation', ''),
            'source_question_id': question.get('source_question_id', ''),
            'source_question_url': question.get('source_question_url', ''),
            'answers': assigned_answers,
        }
        assigned_question['fingerprint'] = compute_question_fingerprint(assigned_question)
        assigned_questions.append(assigned_question)
    return assigned_questions


def validate_question_payloads(question_payloads):
    assigned_questions = assign_positions(question_payloads)
    question_positions = set()
    for question in assigned_questions:
        position = question['position']
        if position in question_positions:
            raise ValidationError({'questions': ['Question positions must be unique.']})
        question_positions.add(position)

        answers = question['answers']
        if len(answers) < 2:
            raise ValidationError({'questions': ['Each question must have at least two answers.']})

        correct_answers = [answer for answer in answers if answer['is_correct']]
        if len(correct_answers) != 1:
            raise ValidationError({'questions': ['Each question must have exactly one correct answer.']})

        answer_positions = set()
        for answer in answers:
            answer_position = answer['position']
            if answer_position in answer_positions:
                raise ValidationError({'questions': ['Answer positions must be unique within a question.']})
            answer_positions.add(answer_position)

    return assigned_questions


def ensure_quiz_schedule_matches_course(course, quiz_data):
    unlock_at = quiz_data.get('unlock_at')
    if course.course_type == Course.CourseType.LIVE and unlock_at is None:
        raise ValidationError({'unlock_at': 'Live course quizzes require an unlock date.'})
    if course.course_type == Course.CourseType.ARCHIVE and unlock_at is not None:
        raise ValidationError({'unlock_at': 'Archive course quizzes cannot set an unlock date.'})


def is_quiz_unlocked(quiz, current_time=None):
    current_time = current_time or timezone.now()
    if quiz.course.course_type == Course.CourseType.ARCHIVE:
        return True
    return bool(quiz.unlock_at and quiz.unlock_at <= current_time)


def get_active_enrollment(course, user):
    return CourseEnrollment.objects.filter(
        course=course,
        user=user,
        is_active=True,
    ).first()


def ensure_course_access(course, user):
    if user.is_staff:
        return None
    if not course.is_published:
        raise PermissionDenied('This course is not published.')
    enrollment = get_active_enrollment(course, user)
    if enrollment is None:
        raise PermissionDenied('You are not enrolled in this course.')
    return enrollment


def ensure_quiz_access(quiz, user, require_unlocked=False):
    ensure_course_access(quiz.course, user)
    if not quiz.is_active:
        raise PermissionDenied('This quiz is not active.')
    if require_unlocked and not is_quiz_unlocked(quiz):
        raise PermissionDenied('This quiz is locked until its unlock date.')


def get_course_performance(course, user):
    active_quiz_ids = list(
        course.quizzes.filter(is_active=True).values_list('id', flat=True)
    )
    total_exams = len(active_quiz_ids)
    attempt_count = CourseQuizAttempt.objects.filter(
        user=user,
        quiz_id__in=active_quiz_ids,
    ).count()

    participant_ids = set(CourseEnrollment.objects.filter(
        course=course,
        is_active=True,
    ).values_list('user_id', flat=True))
    scored_user_ids = participant_ids | {user.id}
    attempts = CourseQuizAttempt.objects.filter(
        quiz_id__in=active_quiz_ids,
        user_id__in=scored_user_ids,
        is_completed=True,
    ).select_related('user').annotate(
        attempted_count=models.Count(
            'submissions',
            filter=models.Q(submissions__selected_answer__isnull=False),
        ),
        correct_count=models.Count(
            'submissions',
            filter=models.Q(
                submissions__selected_answer__isnull=False,
                submissions__is_correct=True,
            ),
        ),
    )

    best_attempts = {}
    for attempt in attempts:
        maximum_score = attempt.total_questions * attempt.correct_mark
        normalized_score = (
            attempt.score * Decimal('100') / maximum_score
            if maximum_score else Decimal('0')
        )
        completion = attempt.end_time or attempt.expires_at
        duration = max((completion - attempt.start_time).total_seconds(), 0)
        key = (-normalized_score, duration, completion, attempt.id)
        current = best_attempts.get((attempt.user_id, attempt.quiz_id))
        if current is None or key < current[0]:
            best_attempts[(attempt.user_id, attempt.quiz_id)] = (
                key,
                attempt,
                normalized_score,
                duration,
                completion,
            )

    selected_by_user = {}
    for (_user_id, _quiz_id), selected in best_attempts.items():
        selected_by_user.setdefault(selected[1].user_id, []).append(selected)

    def rounded(value):
        return float(Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    def make_entry(user_id, selected_attempts):
        attempt = selected_attempts[0][1]
        score_total = sum((selected[2] for selected in selected_attempts), Decimal('0'))
        attempted_total = sum(selected[1].attempted_count for selected in selected_attempts)
        correct_total = sum(selected[1].correct_count for selected in selected_attempts)
        first_name = attempt.user.first_name.strip()
        last_name = attempt.user.last_name.strip()
        display_name = f'{first_name} {last_name}'.strip() or attempt.user.username
        return {
            'rank': None,
            'user_id': user_id,
            'user_display_name': display_name,
            'course_score': rounded(score_total / total_exams) if total_exams else 0.0,
            'exams_completed': len(selected_attempts),
            'total_exams': total_exams,
            'accuracy': rounded(Decimal(correct_total) * 100 / attempted_total) if attempted_total else 0.0,
            'is_current_user': user_id == user.id,
            '_score': score_total / total_exams if total_exams else Decimal('0'),
            '_duration': sum(selected[3] for selected in selected_attempts),
            '_completion': min(selected[4] for selected in selected_attempts),
        }

    entries_by_user = {
        user_id: make_entry(user_id, selected_attempts)
        for user_id, selected_attempts in selected_by_user.items()
    }
    participants = [
        entries_by_user[user_id]
        for user_id in participant_ids
        if user_id in entries_by_user
    ]
    participants.sort(key=lambda entry: (
        -entry['_score'],
        -entry['exams_completed'],
        -entry['accuracy'],
        entry['_duration'],
        entry['_completion'],
        entry['user_id'],
    ))
    for rank, entry in enumerate(participants, start=1):
        entry['rank'] = rank

    current_entry = next(
        (entry for entry in participants if entry['user_id'] == user.id),
        None,
    )
    personal_entry = entries_by_user.get(user.id)
    summary = {
        'rank': current_entry['rank'] if current_entry else None,
        'participant_count': len(participants),
        'course_score': personal_entry['course_score'] if personal_entry else 0.0,
        'exams_completed': personal_entry['exams_completed'] if personal_entry else 0,
        'total_exams': total_exams,
        'attempt_count': attempt_count,
    }

    public_fields = [
        'rank',
        'user_id',
        'user_display_name',
        'course_score',
        'exams_completed',
        'total_exams',
        'accuracy',
        'is_current_user',
    ]

    def public_entry(entry):
        return {field: entry[field] for field in public_fields}

    return {
        'summary': summary,
        'leaderboard': [public_entry(entry) for entry in participants[:10]],
        'current_user_entry': public_entry(current_entry) if current_entry else None,
    }


def create_or_reactivate_enrollment(course, user, source, enrolled_by=None):
    enrollment, created = CourseEnrollment.objects.update_or_create(
        course=course,
        user=user,
        defaults={
            'is_active': True,
            'source': source,
            'enrolled_by': enrolled_by,
        },
    )
    return enrollment, created


def ensure_quiz_can_change_content(quiz):
    if quiz.attempts.exists():
        raise ValidationError('Quiz content cannot be modified after attempts exist.')


def replace_quiz_questions(quiz, question_payloads):
    ensure_quiz_can_change_content(quiz)
    validated_questions = validate_question_payloads(question_payloads)
    quiz.questions.all().delete()
    if not validated_questions:
        return []

    question_objects = CourseQuestion.objects.bulk_create([
        CourseQuestion(
            quiz=quiz,
            position=question['position'],
            question_text=question['question_text'],
            explanation=question.get('explanation', ''),
            source_question_id=question.get('source_question_id', ''),
            source_question_url=question.get('source_question_url', ''),
            fingerprint=question['fingerprint'],
        )
        for question in validated_questions
    ])

    answer_objects = []
    for question_object, question_payload in zip(question_objects, validated_questions):
        for answer in question_payload['answers']:
            answer_objects.append(CourseAnswer(
                question=question_object,
                position=answer['position'],
                text=answer['text'],
                is_correct=answer['is_correct'],
            ))
    CourseAnswer.objects.bulk_create(answer_objects)
    return question_objects


def parse_csv_questions(uploaded_file):
    required_columns = {
        'Item Type',
        'Question Title',
        'Answer Text',
        'Answer Correct/InCorrect',
    }
    try:
        decoded_text = uploaded_file.read().decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValidationError({'file': 'CSV file must be UTF-8 encoded.'}) from exc

    reader = csv.DictReader(io.StringIO(decoded_text))
    if not reader.fieldnames:
        raise ValidationError({'file': 'CSV file is empty.'})
    missing_columns = sorted(required_columns - set(reader.fieldnames))
    if missing_columns:
        raise ValidationError({
            'file': [f'Missing required columns: {", ".join(missing_columns)}'],
        })

    questions = []
    current_question = None
    for row_number, row in enumerate(reader, start=2):
        item_type = normalize_text(row.get('Item Type', '')).lower()
        if not item_type:
            continue
        if item_type == 'question':
            question_title = normalize_text(row.get('Question Title', ''))
            if not question_title:
                raise ValidationError({'file': [f'Row {row_number}: question title is required.']})
            if current_question is not None:
                questions.append(current_question)
            current_question = {
                'question_text': question_title,
                'explanation': normalize_text(row.get('Question Answer Info', '')),
                'answers': [],
            }
            continue
        if item_type == 'answer':
            if current_question is None:
                raise ValidationError({'file': [f'Row {row_number}: answer row appeared before any question row.']})
            answer_text = normalize_text(row.get('Answer Text', ''))
            if not answer_text:
                raise ValidationError({'file': [f'Row {row_number}: answer text is required.']})
            current_question['answers'].append({
                'text': answer_text,
                'is_correct': normalize_text(row.get('Answer Correct/InCorrect', '')) == '1',
            })
            continue
        raise ValidationError({'file': [f'Row {row_number}: unsupported Item Type "{item_type}".']})

    if current_question is not None:
        questions.append(current_question)

    if not questions:
        raise ValidationError({'file': 'CSV file did not contain any questions.'})
    return questions


def append_questions_to_quiz(quiz, question_payloads):
    ensure_quiz_can_change_content(quiz)
    validated_questions = validate_question_payloads(question_payloads)
    existing_fingerprints = set(quiz.questions.values_list('fingerprint', flat=True))
    existing_source_ids = {
        source_question_id
        for source_question_id in quiz.questions.exclude(source_question_id='').values_list('source_question_id', flat=True)
    }

    new_questions = []
    skipped = 0
    next_position = (quiz.questions.aggregate(max_position=models.Max('position'))['max_position'] or 0) + 1
    for question in validated_questions:
        if question['fingerprint'] in existing_fingerprints:
            skipped += 1
            continue
        if question.get('source_question_id') and question['source_question_id'] in existing_source_ids:
            skipped += 1
            continue
        new_questions.append({
            'position': next_position,
            'question_text': question['question_text'],
            'explanation': question.get('explanation', ''),
            'source_question_id': question.get('source_question_id', ''),
            'source_question_url': question.get('source_question_url', ''),
            'fingerprint': question['fingerprint'],
            'answers': question['answers'],
        })
        next_position += 1

    question_objects = CourseQuestion.objects.bulk_create([
        CourseQuestion(
            quiz=quiz,
            position=question['position'],
            question_text=question['question_text'],
            explanation=question['explanation'],
            source_question_id=question['source_question_id'],
            source_question_url=question['source_question_url'],
            fingerprint=question['fingerprint'],
        )
        for question in new_questions
    ])
    answer_objects = []
    for question_object, question_payload in zip(question_objects, new_questions):
        for answer in question_payload['answers']:
            answer_objects.append(CourseAnswer(
                question=question_object,
                position=answer['position'],
                text=answer['text'],
                is_correct=answer['is_correct'],
            ))
    CourseAnswer.objects.bulk_create(answer_objects)
    return {
        'total': len(validated_questions),
        'created': len(question_objects),
        'skipped': skipped,
    }


def create_quiz_from_import(course, quiz_data, question_payloads):
    ensure_quiz_schedule_matches_course(course, quiz_data)
    with transaction.atomic():
        lookup = {'course': course, 'name': quiz_data['name']}
        if quiz_data.get('source_name') and quiz_data.get('external_quiz_id'):
            lookup = {
                'course': course,
                'source_name': quiz_data['source_name'],
                'external_quiz_id': quiz_data['external_quiz_id'],
            }

        quiz = CourseQuiz.objects.filter(**lookup).first()
        quiz_created = False
        if quiz is None:
            next_position = (
                course.quizzes.aggregate(max_position=models.Max('position'))['max_position'] or 0
            ) + 1
            quiz = CourseQuiz.objects.create(
                course=course,
                name=quiz_data['name'],
                topic=quiz_data.get('topic', ''),
                quiz_type=quiz_data.get('quiz_type', CourseQuiz.QuizType.QUESTION_BANK),
                position=next_position,
                duration_minutes=quiz_data['duration_minutes'],
                correct_mark=quiz_data.get('correct_mark', Decimal('1.00')),
                wrong_mark=quiz_data.get('wrong_mark', Decimal('0')),
                unanswered_mark=quiz_data.get('unanswered_mark', Decimal('0')),
                unlock_at=quiz_data.get('unlock_at'),
                is_active=quiz_data.get('is_active', True),
                source_name=quiz_data.get('source_name', ''),
                external_quiz_id=quiz_data.get('external_quiz_id', ''),
                source_url=quiz_data.get('source_url', ''),
            )
            quiz_created = True
        else:
            ensure_quiz_schedule_matches_course(course, {'unlock_at': quiz.unlock_at})
            updated_fields = []
            for field in [
                'name',
                'topic',
                'quiz_type',
                'duration_minutes',
                'correct_mark',
                'wrong_mark',
                'unanswered_mark',
                'unlock_at',
                'is_active',
                'source_name',
                'external_quiz_id',
                'source_url',
            ]:
                if field in quiz_data and getattr(quiz, field) != quiz_data[field]:
                    setattr(quiz, field, quiz_data[field])
                    updated_fields.append(field)
            if updated_fields:
                quiz.save(update_fields=updated_fields + ['updated_at'])

        import_result = append_questions_to_quiz(quiz, question_payloads)
        return {
            'quiz': quiz,
            'quiz_created': quiz_created,
            **import_result,
        }


def expire_attempt(attempt):
    if attempt.is_completed:
        return attempt
    attempt.score = attempt.total_questions * attempt.unanswered_mark
    attempt.is_completed = True
    attempt.end_time = timezone.now()
    attempt.save(update_fields=['score', 'is_completed', 'end_time'])
    return attempt


def create_or_resume_attempt(quiz, user):
    with transaction.atomic():
        attempt = CourseQuizAttempt.objects.select_for_update().filter(
            quiz=quiz,
            user=user,
            is_completed=False,
        ).first()
        if attempt is not None:
            if timezone.now() >= attempt.expires_at:
                expire_attempt(attempt)
            else:
                return attempt, False

        question_count = quiz.questions.count()
        if question_count == 0:
            raise ValidationError('Quiz must contain at least one question before starting an attempt.')

        attempt = CourseQuizAttempt.objects.create(
            user=user,
            quiz=quiz,
            expires_at=timezone.now() + timedelta(minutes=quiz.duration_minutes),
            total_questions=question_count,
            correct_mark=quiz.correct_mark,
            wrong_mark=quiz.wrong_mark,
            unanswered_mark=quiz.unanswered_mark,
        )
        return attempt, True


def attempt_is_expired(attempt):
    return not attempt.is_completed and timezone.now() >= attempt.expires_at


def validate_submission_payload(attempt, submissions_data):
    quiz_questions = list(attempt.quiz.questions.all().order_by('position', 'id'))
    quiz_question_ids = {question.id for question in quiz_questions}
    submitted_ids = [submission['question_id'] for submission in submissions_data]
    submitted_id_set = set(submitted_ids)

    seen_ids = set()
    duplicate_ids = set()
    for question_id in submitted_ids:
        if question_id in seen_ids:
            duplicate_ids.add(question_id)
        seen_ids.add(question_id)
    duplicate_ids = sorted(duplicate_ids)
    if duplicate_ids or submitted_id_set != quiz_question_ids or len(submitted_ids) != len(quiz_question_ids):
        raise ValidationError({
            'error': 'Submit exactly one row for every quiz question.',
            'missing_question_ids': sorted(quiz_question_ids - submitted_id_set),
            'unexpected_question_ids': sorted(submitted_id_set - quiz_question_ids),
            'duplicate_question_ids': duplicate_ids,
        })

    answer_ids = {
        submission['selected_answer_id']
        for submission in submissions_data
        if submission['selected_answer_id'] is not None
    }
    answers_by_id = {
        answer.id: answer
        for answer in CourseAnswer.objects.filter(id__in=answer_ids).select_related('question')
    }

    submissions_to_create = []
    answer_map = {}
    for index, submission in enumerate(submissions_data):
        question_id = submission['question_id']
        selected_answer_id = submission['selected_answer_id']
        answer = answers_by_id.get(selected_answer_id)
        if selected_answer_id is not None and (
            answer is None or answer.question_id != question_id
        ):
            raise ValidationError({
                'submissions': {
                    str(index): ['Selected answer does not belong to the submitted question.'],
                },
            })
        submissions_to_create.append(CourseQuizSubmission(
            attempt=attempt,
            question_id=question_id,
            selected_answer_id=selected_answer_id,
            is_correct=bool(answer and answer.is_correct),
        ))
        answer_map[question_id] = selected_answer_id

    correct_count = sum(1 for submission in submissions_to_create if submission.is_correct)
    wrong_count = sum(
        1
        for submission in submissions_to_create
        if submission.selected_answer_id is not None and not submission.is_correct
    )
    unanswered_count = sum(
        1 for submission in submissions_to_create if submission.selected_answer_id is None
    )

    return {
        'submissions_to_create': submissions_to_create,
        'submitted_answers_by_question_id': answer_map,
        'correct_count': correct_count,
        'wrong_count': wrong_count,
        'unanswered_count': unanswered_count,
    }


def submissions_match_attempt(attempt, submitted_answers_by_question_id):
    existing_answers = dict(
        CourseQuizSubmission.objects.filter(attempt=attempt).values_list(
            'question_id',
            'selected_answer_id',
        )
    )
    return existing_answers == submitted_answers_by_question_id


def finalize_attempt_submission(attempt, submission_state):
    CourseQuizSubmission.objects.filter(attempt=attempt).delete()
    CourseQuizSubmission.objects.bulk_create(submission_state['submissions_to_create'])
    attempt.score = (
        submission_state['correct_count'] * attempt.correct_mark
        + submission_state['wrong_count'] * attempt.wrong_mark
        + submission_state['unanswered_count'] * attempt.unanswered_mark
    )
    attempt.is_completed = True
    attempt.end_time = timezone.now()
    attempt.save(update_fields=['score', 'is_completed', 'end_time'])
    return attempt
