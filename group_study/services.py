"""Question snapshotting, membership resolution and notification helpers."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from api.models import Answer, Question

from .models import (
    GroupStudyAnswer,
    GroupStudyQuestion,
    GroupStudyQuizAttempt,
    GroupStudyQuizSubmission,
    StudyGroupMembership,
)


User = get_user_model()


# ── Membership helpers ───────────────────────────────────────────────────────

def get_membership(group, user):
    return group.memberships.filter(user=user).first()


def ensure_member(group, user):
    membership = get_membership(group, user)
    if membership is None:
        raise PermissionDenied('You are not a member of this group.')
    return membership


def ensure_admin(group, user):
    membership = ensure_member(group, user)
    if membership.role != StudyGroupMembership.Role.ADMIN:
        raise PermissionDenied('Group admin access is required.')
    return membership


def ensure_quiz_manager(quiz, user):
    """Admins can manage every quiz; other members only the ones they created."""
    membership = ensure_member(quiz.group, user)
    if (
        membership.role == StudyGroupMembership.Role.ADMIN
        or quiz.created_by_id == user.id
    ):
        return membership
    raise PermissionDenied(
        'Only the quiz creator or a group admin can manage this quiz.'
    )


def add_membership(group, user, role=StudyGroupMembership.Role.MEMBER):
    """Create the membership once; re-adding a member never changes their role.

    New members start with ``last_read_at`` set so joining/being added does not
    backfill the whole message history as unread.
    """
    membership, created = StudyGroupMembership.objects.get_or_create(
        group=group,
        user=user,
        defaults={'role': role, 'last_read_at': timezone.now()},
    )
    return membership, created


# ── Question snapshotting ────────────────────────────────────────────────────

def snapshot_questions(group_quiz, question_ids):
    """Copy api.Question + api.Answer rows into the group quiz's own tables.

    Positions are assigned sequentially after the quiz's existing questions so
    this is safe to call for both initial creation and later appends. Answers
    keep their source ordering by id.
    """
    questions = list(
        Question.objects.filter(id__in=question_ids).prefetch_related(
            models.Prefetch('answers', queryset=Answer.objects.order_by('id')),
        )
    )
    if not questions:
        return []

    question_by_id = {question.id: question for question in questions}
    ordered = [question_by_id[qid] for qid in question_ids if qid in question_by_id]

    next_position = (
        group_quiz.questions.aggregate(max_position=models.Max('position'))['max_position'] or 0
    ) + 1

    question_objects = []
    for question in ordered:
        question_objects.append(GroupStudyQuestion(
            quiz=group_quiz,
            position=next_position,
            question_text=question.question_text,
            explanation=question.explanation or '',
            source_question_id=question.id,
        ))
        next_position += 1

    GroupStudyQuestion.objects.bulk_create(question_objects)

    answer_objects = []
    for question_object, question in zip(question_objects, ordered):
        for answer_index, answer in enumerate(list(question.answers.all()), start=1):
            answer_objects.append(GroupStudyAnswer(
                question=question_object,
                position=answer_index,
                text=answer.text,
                is_correct=answer.is_correct,
            ))
    GroupStudyAnswer.objects.bulk_create(answer_objects)
    return question_objects


def snapshot_quiz_questions(group_quiz, api_quiz):
    """Copy every question from an existing api.Quiz into the group quiz."""
    question_ids = list(api_quiz.questions.values_list('id', flat=True))
    return snapshot_questions(group_quiz, question_ids)


def validate_manual_questions(question_payloads):
    """Validate hand-written question payloads and assign positions."""
    validated = []
    question_positions = set()
    for index, question in enumerate(question_payloads, start=1):
        position = question.get('position') or index
        if position in question_positions:
            raise ValidationError({'questions': ['Question positions must be unique.']})
        question_positions.add(position)

        answers = question['answers']
        if len(answers) < 2:
            raise ValidationError({'questions': ['Each question must have at least two answers.']})
        if sum(1 for answer in answers if answer['is_correct']) != 1:
            raise ValidationError({'questions': ['Each question must have exactly one correct answer.']})

        assigned_answers = []
        answer_positions = set()
        for answer_index, answer in enumerate(answers, start=1):
            answer_position = answer.get('position') or answer_index
            if answer_position in answer_positions:
                raise ValidationError({'questions': ['Answer positions must be unique within a question.']})
            answer_positions.add(answer_position)
            assigned_answers.append({
                'position': answer_position,
                'text': answer['text'],
                'is_correct': answer['is_correct'],
            })

        validated.append({
            'position': position,
            'question_text': question['question_text'],
            'explanation': question.get('explanation', ''),
            'answers': assigned_answers,
        })
    return validated


def create_manual_questions(group_quiz, question_payloads):
    """Persist hand-written questions (and their answers) into the group quiz."""
    validated = validate_manual_questions(question_payloads)
    if not validated:
        return []

    next_position = (
        group_quiz.questions.aggregate(max_position=models.Max('position'))['max_position'] or 0
    ) + 1

    question_objects = []
    for question in validated:
        question_objects.append(GroupStudyQuestion(
            quiz=group_quiz,
            position=next_position,
            question_text=question['question_text'],
            explanation=question['explanation'],
        ))
        next_position += 1
    GroupStudyQuestion.objects.bulk_create(question_objects)

    answer_objects = []
    for question_object, question in zip(question_objects, validated):
        for answer in question['answers']:
            answer_objects.append(GroupStudyAnswer(
                question=question_object,
                position=answer['position'],
                text=answer['text'],
                is_correct=answer['is_correct'],
            ))
    GroupStudyAnswer.objects.bulk_create(answer_objects)
    return question_objects


# ── Email resolution (registered users only) ────────────────────────────────

def resolve_members_by_emails(emails):
    """Map emails to existing users. Returns ``(users, not_found_emails)``."""
    normalized = [email.strip().lower() for email in emails if email and email.strip()]
    if not normalized:
        return [], []
    query = models.Q()
    for email in normalized:
        query |= models.Q(email__iexact=email)
    users = list(User.objects.filter(query))
    found_lower = {user.email.strip().lower() for user in users if user.email}
    not_found = [email for email in normalized if email not in found_lower]
    return users, not_found


# ── Notifications ────────────────────────────────────────────────────────────

def notify_user(user, title, body, kind, route='', payload=None, data=None):
    """Push + in-app notification to a single registered user's devices."""
    from notifications.models import DeviceInstallation
    from notifications.services import send_direct

    installations = DeviceInstallation.objects.filter(
        is_active=True,
        user=user,
    ).only('token', 'user_id')
    return send_direct(
        installations,
        title=title,
        body=body,
        kind=kind,
        route=route,
        payload=payload,
        data=data,
    )


def notify_members(group, title, body, kind, route='', payload=None, exclude_user=None,
                   respect_chat_preference=False):
    """Push + in-app notification to all group members (optionally minus one).

    When ``respect_chat_preference`` is set, members who muted chat
    notifications for this group are skipped.
    """
    from notifications.models import DeviceInstallation
    from notifications.services import send_direct

    payload = payload or {}
    memberships = group.memberships.all()
    if respect_chat_preference:
        memberships = memberships.filter(notify_messages=True)
    member_ids = memberships.values_list('user_id', flat=True)
    if exclude_user is not None:
        member_ids = member_ids.exclude(user_id=exclude_user.id)

    installations = DeviceInstallation.objects.filter(
        is_active=True,
        user_id__in=list(member_ids),
    ).only('token', 'user_id')

    data = {'group_id': group.id}
    if payload.get('quiz_id'):
        data['quiz_id'] = payload['quiz_id']

    return send_direct(
        installations,
        title=title,
        body=body,
        kind=kind,
        route=route,
        payload=payload,
        data=data,
    )


# ── Schedule / attempt lifecycle ────────────────────────────────────────────

def ensure_quiz_available(quiz, now=None):
    now = now or timezone.now()
    if not quiz.is_published:
        raise PermissionDenied('This quiz has not been published yet.')
    if now < quiz.start_at:
        raise PermissionDenied('This quiz has not started yet.')
    if now > quiz.end_at:
        raise PermissionDenied('This quiz has ended.')


def attempt_is_expired(attempt):
    return not attempt.is_completed and timezone.now() >= attempt.expires_at


def expire_attempt(attempt):
    if attempt.is_completed:
        return attempt
    attempt.score = attempt.total_questions * attempt.unanswered_mark
    attempt.is_completed = True
    attempt.end_time = timezone.now()
    attempt.save(update_fields=['score', 'is_completed', 'end_time'])
    return attempt


def create_or_resume_attempt(quiz, user):
    ensure_quiz_available(quiz)
    with transaction.atomic():
        attempt = GroupStudyQuizAttempt.objects.select_for_update().filter(
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

        attempt = GroupStudyQuizAttempt.objects.create(
            user=user,
            quiz=quiz,
            expires_at=timezone.now() + timedelta(minutes=quiz.duration_minutes),
            total_questions=question_count,
            correct_mark=quiz.correct_mark,
            wrong_mark=quiz.wrong_mark,
            unanswered_mark=quiz.unanswered_mark,
        )
        return attempt, True


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
        for answer in GroupStudyAnswer.objects.filter(id__in=answer_ids).select_related('question')
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
        submissions_to_create.append(GroupStudyQuizSubmission(
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
        GroupStudyQuizSubmission.objects.filter(attempt=attempt).values_list(
            'question_id',
            'selected_answer_id',
        )
    )
    return existing_answers == submitted_answers_by_question_id


def finalize_attempt_submission(attempt, submission_state):
    GroupStudyQuizSubmission.objects.filter(attempt=attempt).delete()
    GroupStudyQuizSubmission.objects.bulk_create(submission_state['submissions_to_create'])
    attempt.score = (
        submission_state['correct_count'] * attempt.correct_mark
        + submission_state['wrong_count'] * attempt.wrong_mark
        + submission_state['unanswered_count'] * attempt.unanswered_mark
    )
    attempt.is_completed = True
    attempt.end_time = timezone.now()
    attempt.save(update_fields=['score', 'is_completed', 'end_time'])
    return attempt
