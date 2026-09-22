from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import (
    BooleanField,
    Count,
    DateTimeField,
    Exists,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import Quiz
from api.pagination import StandardResultsSetPagination
from api.permissions import IsAuthenticatedUserOnly
from notifications.models import NotificationKind

from .models import (
    GroupMessage,
    GroupStudyAnswer,
    GroupStudyQuestion,
    GroupStudyQuiz,
    GroupStudyQuizAttempt,
    GroupStudyQuizSubmission,
    StudyGroup,
    StudyGroupMembership,
)
from .serializers import (
    BulkGroupSubmissionSerializer,
    CandidateGroupStudyQuizDetailSerializer,
    GroupMessageNotificationSerializer,
    GroupMessageSerializer,
    GroupMessageWriteSerializer,
    GroupStudyMemberAddSerializer,
    GroupStudyQuizAddQuestionsSerializer,
    GroupStudyQuizAttemptResultSerializer,
    GroupStudyQuizAttemptStartSerializer,
    GroupStudyQuizDetailSerializer,
    GroupStudyQuizSummarySerializer,
    GroupStudyQuizWriteSerializer,
    PublicGroupSerializer,
    StudyGroupDetailSerializer,
    StudyGroupMembershipSerializer,
    StudyGroupSerializer,
    StudyGroupWriteSerializer,
    decimal_as_number,
)
from .services import (
    add_membership,
    attempt_is_expired,
    create_manual_questions,
    create_or_resume_attempt,
    ensure_admin,
    ensure_member,
    ensure_quiz_available,
    ensure_quiz_manager,
    finalize_attempt_submission,
    get_membership,
    notify_members,
    notify_user,
    resolve_members_by_emails,
    snapshot_questions,
    snapshot_quiz_questions,
    submissions_match_attempt,
    validate_submission_payload,
)


# ── Queryset helpers ─────────────────────────────────────────────────────────

def get_unread_message_count_expression(user):
    """Per-group count of messages the user has not read yet.

    A membership with ``last_read_at = null`` has never opened the chat, so
    every message from another sender counts as unread.
    """
    last_read_subquery = StudyGroupMembership.objects.filter(
        group=OuterRef('pk'),
        user=user,
    ).values('last_read_at')[:1]
    unread_subquery = GroupMessage.objects.filter(
        group=OuterRef('pk'),
        created_at__gt=Coalesce(
            Subquery(last_read_subquery),
            Value(datetime(1970, 1, 1, tzinfo=dt_timezone.utc)),
            output_field=DateTimeField(),
        ),
    ).exclude(sender=user).order_by().values('group').annotate(
        total=Count('id'),
    ).values('total')
    return Coalesce(
        Subquery(unread_subquery),
        Value(0),
        output_field=IntegerField(),
    )


def _group_count_subquery(model):
    """COUNT(*) of a group's related rows without a GROUP BY join.

    Subqueries keep the outer query flat, so pagination's COUNT(*) stays an
    index-only scan over StudyGroup instead of aggregating every related row.
    """
    return Subquery(
        model.objects.filter(group=OuterRef('pk'))
        .order_by()
        .values('group')
        .annotate(total=Count('id'))
        .values('total'),
        output_field=IntegerField(),
    )


def get_group_base_queryset(user):
    role_subquery = StudyGroupMembership.objects.filter(
        group=OuterRef('pk'),
        user=user,
    ).values('role')[:1]
    notify_messages_subquery = StudyGroupMembership.objects.filter(
        group=OuterRef('pk'),
        user=user,
    ).values('notify_messages')[:1]
    membership_exists = StudyGroupMembership.objects.filter(
        group=OuterRef('pk'),
        user=user,
    )
    return StudyGroup.objects.select_related('created_by').annotate(
        member_count=Coalesce(
            _group_count_subquery(StudyGroupMembership),
            Value(0),
            output_field=IntegerField(),
        ),
        quiz_count=Coalesce(
            _group_count_subquery(GroupStudyQuiz),
            Value(0),
            output_field=IntegerField(),
        ),
        my_role=Subquery(role_subquery),
        my_notify_messages=Subquery(
            notify_messages_subquery,
            output_field=BooleanField(),
        ),
        unread_message_count=get_unread_message_count_expression(user),
        is_member=Exists(membership_exists),
    )


def get_group_list_queryset(user):
    return get_group_base_queryset(user).filter(is_member=True).order_by('name', 'id')


def get_public_group_queryset(user):
    return get_group_base_queryset(user).filter(
        is_public=True,
        is_active=True,
    ).order_by('name', 'id')


def get_group_detail_queryset(user):
    # Only a preview of members travels with the group; the Members tab pages
    # through the members endpoint.
    return get_group_list_queryset(user).prefetch_related(
        Prefetch(
            'memberships',
            queryset=StudyGroupMembership.objects.select_related('user').order_by('joined_at', 'id')[:50],
            to_attr='member_preview',
        ),
    )


def get_quiz_queryset(user, include_questions=False):
    active_attempt = GroupStudyQuizAttempt.objects.filter(
        quiz_id=OuterRef('pk'),
        user=user,
        is_completed=False,
    )
    question_count_subquery = (
        GroupStudyQuestion.objects.filter(quiz=OuterRef('pk'))
        .order_by()
        .values('quiz')
        .annotate(total=Count('id'))
        .values('total')
    )
    completed_attempt_count_subquery = (
        GroupStudyQuizAttempt.objects.filter(
            quiz=OuterRef('pk'),
            user=user,
            is_completed=True,
        )
        .order_by()
        .values('quiz')
        .annotate(total=Count('id'))
        .values('total')
    )
    # Subquery counts avoid the joins that would otherwise force a GROUP BY over
    # every question/attempt row of every quiz on the page.
    queryset = GroupStudyQuiz.objects.select_related('group', 'created_by').annotate(
        question_count=Coalesce(
            Subquery(question_count_subquery, output_field=IntegerField()),
            Value(0),
            output_field=IntegerField(),
        ),
        completed_attempt_count=Coalesce(
            Subquery(completed_attempt_count_subquery, output_field=IntegerField()),
            Value(0),
            output_field=IntegerField(),
        ),
        has_in_progress_attempt=Exists(active_attempt),
    ).order_by('-created_at', '-id')
    if include_questions:
        queryset = queryset.prefetch_related(
            Prefetch(
                'questions',
                queryset=GroupStudyQuestion.objects.order_by('position', 'id').prefetch_related(
                    Prefetch('answers', queryset=GroupStudyAnswer.objects.order_by('position', 'id')),
                ),
            ),
        )
    return queryset


def get_attempt_result_queryset(user):
    return GroupStudyQuizAttempt.objects.select_related(
        'quiz',
        'quiz__group',
        'user',
    ).prefetch_related(
        Prefetch(
            'quiz__questions',
            queryset=GroupStudyQuestion.objects.order_by('position', 'id').prefetch_related(
                Prefetch('answers', queryset=GroupStudyAnswer.objects.order_by('position', 'id')),
            ),
        ),
        Prefetch(
            'submissions',
            queryset=GroupStudyQuizSubmission.objects.select_related(
                'selected_answer',
                'question',
            ).prefetch_related(
                Prefetch('question__answers', queryset=GroupStudyAnswer.objects.order_by('position', 'id')),
            ).order_by('question__position', 'id'),
        ),
    )


def get_group_object(group_id):
    return get_object_or_404(StudyGroup, pk=group_id)


# ── Group views ──────────────────────────────────────────────────────────────

class StudyGroupListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return get_group_list_queryset(self.request.user)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return StudyGroupWriteSerializer
        return StudyGroupSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = serializer.save(created_by=request.user)
        add_membership(group, request.user, role=StudyGroupMembership.Role.ADMIN)
        group = get_group_detail_queryset(request.user).get(pk=group.pk)
        return Response(
            StudyGroupDetailSerializer(group, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class StudyGroupDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    lookup_url_kwarg = 'group_id'

    def get_queryset(self):
        return get_group_detail_queryset(self.request.user)

    def get_serializer_class(self):
        if self.request.method in permissions.SAFE_METHODS:
            return StudyGroupDetailSerializer
        return StudyGroupWriteSerializer

    def get_object(self):
        group = super().get_object()
        ensure_member(group, self.request.user)
        return group

    def update(self, request, *args, **kwargs):
        group = self.get_object()
        ensure_admin(group, request.user)
        partial = kwargs.pop('partial', False)
        serializer = self.get_serializer(group, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # Respond with the full detail object, not the short write payload, so
        # clients can refresh their group state in one round trip.
        group = get_group_detail_queryset(request.user).get(pk=group.pk)
        return Response(
            StudyGroupDetailSerializer(group, context=self.get_serializer_context()).data,
        )

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        group = self.get_object()
        ensure_admin(group, request.user)
        return super().destroy(request, *args, **kwargs)


class PublicGroupListView(generics.ListAPIView):
    """Active public groups that anyone can discover and join."""

    permission_classes = [IsAuthenticatedUserOnly]
    pagination_class = StandardResultsSetPagination
    serializer_class = PublicGroupSerializer

    def get_queryset(self):
        queryset = get_public_group_queryset(self.request.user)
        search = (self.request.query_params.get('search') or '').strip()
        if search:
            # Prefix search keeps the (is_public, name, id) index usable.
            queryset = queryset.filter(name__istartswith=search)
        return queryset


class StudyGroupJoinView(APIView):
    """Self-join a public group. Private groups stay invite-only."""

    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, group_id):
        group = get_group_object(group_id)
        if not group.is_public:
            raise PermissionDenied(
                'This group is private. Ask a member to add you.'
            )
        if not group.is_active:
            return Response(
                {'error': 'This group is inactive.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _membership, created = add_membership(group, request.user)
        group = get_group_detail_queryset(request.user).get(pk=group.pk)
        return Response(
            StudyGroupDetailSerializer(group, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class StudyGroupLeaveView(APIView):
    """Members can leave a group; admins must delete it instead."""

    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, group_id):
        group = get_group_object(group_id)
        membership = ensure_member(group, request.user)
        if membership.role == StudyGroupMembership.Role.ADMIN:
            return Response(
                {'error': 'Group admins cannot leave. Delete the group instead.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StudyGroupMemberListView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]
    pagination_class = StandardResultsSetPagination

    def get_group(self):
        group = get_group_object(self.kwargs['group_id'])
        ensure_member(group, self.request.user)
        return group

    def get(self, request, group_id):
        group = self.get_group()
        memberships = group.memberships.select_related('user').order_by('joined_at', 'id')
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(memberships, request, view=self)
        serializer = StudyGroupMembershipSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request, group_id):
        group = self.get_group()
        # Any member can invite classmates in both public and private groups.
        ensure_member(group, request.user)
        serializer = GroupStudyMemberAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        users, not_found = resolve_members_by_emails(serializer.validated_data['emails'])
        if not_found:
            return Response(
                {
                    'error': 'No registered user found for these emails.',
                    'not_found_emails': not_found,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        added = []
        existing = []
        for user in users:
            membership, created = add_membership(group, user)
            if created:
                added.append(user)
                notify_user(
                    user,
                    title='Added to a study group',
                    body=f'{request.user.username} added you to "{group.name}".',
                    kind=NotificationKind.GROUP_STUDY_ADDED,
                    route='/group-study',
                    payload={'group_id': group.id},
                )
            else:
                existing.append(user)

        response_serializer = StudyGroupMembershipSerializer(
            group.memberships.select_related('user').filter(user__in=users),
            many=True,
        )
        return Response({
            'added_count': len(added),
            'already_member_count': len(existing),
            'memberships': response_serializer.data,
        }, status=status.HTTP_200_OK)


class StudyGroupMemberDetailView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def delete(self, request, group_id, member_id):
        group = get_group_object(group_id)
        ensure_admin(group, request.user)
        membership = get_object_or_404(
            StudyGroupMembership,
            pk=member_id,
            group=group,
        )
        if membership.user_id == request.user.id:
            return Response(
                {'error': 'You cannot remove yourself from the group.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupMessageListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    pagination_class = StandardResultsSetPagination

    def get_group(self):
        if not hasattr(self, '_group'):
            group = get_group_object(self.kwargs['group_id'])
            self._membership = ensure_member(group, self.request.user)
            self._group = group
        return self._group

    def get_queryset(self):
        group = self.get_group()
        return group.messages.select_related('sender').order_by('created_at', 'id')

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return GroupMessageWriteSerializer
        return GroupMessageSerializer

    def create(self, request, *args, **kwargs):
        group = self.get_group()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data['body']
        message = GroupMessage.objects.create(group=group, sender=request.user, body=body)

        # Sending implies the sender has seen the conversation, so their own
        # messages never show up as unread.
        self._membership.last_read_at = timezone.now()
        self._membership.save(update_fields=['last_read_at', 'updated_at'])

        sender_name = request.user.get_full_name() or request.user.username
        notify_members(
            group,
            title=f'{sender_name} · {group.name}',
            body=body[:200],
            kind=NotificationKind.GROUP_STUDY_MESSAGE,
            route='/group-study',
            payload={'group_id': group.id, 'message_id': message.id},
            exclude_user=request.user,
            respect_chat_preference=True,
        )

        response_serializer = GroupMessageSerializer(message, context=self.get_serializer_context())
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class GroupMessageReadView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, group_id):
        group = get_group_object(group_id)
        membership = ensure_member(group, request.user)
        membership.last_read_at = timezone.now()
        membership.save(update_fields=['last_read_at', 'updated_at'])
        return Response({
            'detail': 'Messages marked as read.',
            'unread_message_count': 0,
        })


class GroupMessageNotificationView(APIView):
    """Turn chat push notifications on or off for the requesting member."""

    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, group_id):
        group = get_group_object(group_id)
        membership = ensure_member(group, request.user)
        serializer = GroupMessageNotificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership.notify_messages = serializer.validated_data['notify_messages']
        membership.save(update_fields=['notify_messages', 'updated_at'])
        return Response({
            'detail': (
                'Chat notifications enabled.'
                if membership.notify_messages
                else 'Chat notifications disabled.'
            ),
            'notify_messages': membership.notify_messages,
        })


# ── Quiz views ───────────────────────────────────────────────────────────────

class GroupStudyQuizListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    pagination_class = StandardResultsSetPagination

    def get_group(self):
        if not hasattr(self, '_group'):
            group = get_group_object(self.kwargs['group_id'])
            self._membership = ensure_member(group, self.request.user)
            self._group = group
        return self._group

    def is_admin(self):
        self.get_group()
        return self._membership.role == StudyGroupMembership.Role.ADMIN

    def get_queryset(self):
        group = self.get_group()
        # The list serializer never renders question content, so skip the
        # expensive questions/answers prefetch here. Detail endpoints load
        # questions instead.
        queryset = get_quiz_queryset(
            self.request.user,
            include_questions=False,
        ).filter(group=group)
        if not self.is_admin():
            # Members see published quizzes plus their own drafts.
            queryset = queryset.filter(
                Q(is_published=True) | Q(created_by=self.request.user),
            )
        return queryset

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return GroupStudyQuizWriteSerializer
        return GroupStudyQuizSummarySerializer

    def create(self, request, *args, **kwargs):
        group = self.get_group()
        membership = ensure_member(group, request.user)
        # Admins can restrict exam creation to themselves in group settings.
        if (
            membership.role != StudyGroupMembership.Role.ADMIN
            and not group.members_can_create_quizzes
        ):
            raise PermissionDenied(
                'Only group admins can create exams in this group.'
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question_ids = serializer.validated_data.pop('question_ids', None)
        source_quiz_id = serializer.validated_data.pop('source_quiz_id', None)
        questions = serializer.validated_data.pop('questions', None)
        with transaction.atomic():
            quiz = GroupStudyQuiz.objects.create(
                group=group,
                created_by=request.user,
                **serializer.validated_data,
            )
            if source_quiz_id:
                api_quiz = get_object_or_404(Quiz, pk=source_quiz_id)
                quiz.source_quiz = api_quiz
                quiz.save(update_fields=['source_quiz', 'updated_at'])
                snapshot_quiz_questions(quiz, api_quiz)
            elif question_ids:
                snapshot_questions(quiz, question_ids)
            elif questions:
                create_manual_questions(quiz, questions)

        quiz = get_quiz_queryset(request.user, include_questions=True).get(pk=quiz.pk)
        return Response(
            GroupStudyQuizDetailSerializer(quiz, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class GroupStudyQuizDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    lookup_url_kwarg = 'quiz_id'

    def get_queryset(self):
        return get_quiz_queryset(self.request.user, include_questions=True)

    def get_object(self):
        quiz = super().get_object()
        membership = ensure_member(quiz.group, self.request.user)
        self._is_admin = membership.role == StudyGroupMembership.Role.ADMIN
        self._can_manage = (
            self._is_admin or quiz.created_by_id == self.request.user.id
        )
        if not self._can_manage and not quiz.is_published:
            self.permission_denied(self.request, message='This quiz has not been published yet.')
        return quiz

    def get_serializer_class(self):
        if self.request.method in permissions.SAFE_METHODS:
            if getattr(self, '_can_manage', False):
                return GroupStudyQuizDetailSerializer
            return CandidateGroupStudyQuizDetailSerializer
        return GroupStudyQuizWriteSerializer

    def update(self, request, *args, **kwargs):
        quiz = self.get_object()
        ensure_quiz_manager(quiz, request.user)
        partial = kwargs.pop('partial', False)
        serializer = self.get_serializer(quiz, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('question_ids', None)
        serializer.validated_data.pop('source_quiz_id', None)
        quiz = serializer.save()
        return Response(
            GroupStudyQuizDetailSerializer(quiz, context=self.get_serializer_context()).data,
        )

    def destroy(self, request, *args, **kwargs):
        quiz = self.get_object()
        ensure_quiz_manager(quiz, request.user)
        quiz.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupStudyQuizAddQuestionsView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, quiz_id):
        quiz = get_object_or_404(
            get_quiz_queryset(request.user, include_questions=True),
            pk=quiz_id,
        )
        ensure_quiz_manager(quiz, request.user)
        serializer = GroupStudyQuizAddQuestionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            if serializer.validated_data.get('source_quiz_id'):
                api_quiz = get_object_or_404(Quiz, pk=serializer.validated_data['source_quiz_id'])
                snapshot_quiz_questions(quiz, api_quiz)
            elif serializer.validated_data.get('question_ids'):
                snapshot_questions(quiz, serializer.validated_data['question_ids'])
            else:
                create_manual_questions(quiz, serializer.validated_data['questions'])

        quiz = get_quiz_queryset(request.user, include_questions=True).get(pk=quiz.pk)
        return Response(GroupStudyQuizDetailSerializer(quiz, context={'request': request}).data)


class GroupStudyQuizPublishView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, quiz_id):
        quiz = get_object_or_404(
            GroupStudyQuiz.objects.select_related('group', 'created_by'),
            pk=quiz_id,
        )
        ensure_quiz_manager(quiz, request.user)
        if quiz.questions.count() == 0:
            return Response(
                {'error': 'Cannot publish a quiz with no questions.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not quiz.is_published:
            quiz.is_published = True
            quiz.save(update_fields=['is_published', 'updated_at'])

        notify_members(
            quiz.group,
            title='New quiz published',
            body=f'"{quiz.name}" is now available in {quiz.group.name}.',
            kind=NotificationKind.GROUP_STUDY_PUBLISH,
            route='/group-study',
            payload={'group_id': quiz.group_id, 'quiz_id': quiz.id},
            exclude_user=request.user,
        )
        return Response({'detail': 'Quiz published.', 'is_published': True})


# ── Attempt views ────────────────────────────────────────────────────────────

class GroupStudyQuizAttemptStartView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, quiz_id):
        quiz = get_object_or_404(
            get_quiz_queryset(request.user, include_questions=True),
            pk=quiz_id,
        )
        ensure_member(quiz.group, request.user)
        attempt, _created = create_or_resume_attempt(quiz, request.user)
        attempt.quiz = get_quiz_queryset(request.user, include_questions=True).get(pk=quiz.pk)
        serializer = GroupStudyQuizAttemptStartSerializer(attempt)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GroupStudyQuizAttemptSubmitView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, attempt_id):
        input_serializer = BulkGroupSubmissionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            attempt = get_object_or_404(
                GroupStudyQuizAttempt.objects.select_for_update().select_related('quiz', 'quiz__group'),
                pk=attempt_id,
                user=request.user,
            )
            ensure_quiz_available(attempt.quiz)
            submission_state = validate_submission_payload(
                attempt,
                input_serializer.validated_data['submissions'],
            )

            if attempt.is_completed:
                if not submissions_match_attempt(
                    attempt,
                    submission_state['submitted_answers_by_question_id'],
                ):
                    return Response(
                        {'error': 'This quiz attempt has already been submitted with different answers.'},
                        status=status.HTTP_409_CONFLICT,
                    )
            else:
                if attempt_is_expired(attempt):
                    return Response(
                        {'error': 'This quiz attempt has expired.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                finalize_attempt_submission(attempt, submission_state)

        result_attempt = get_attempt_result_queryset(request.user).get(pk=attempt_id)
        serializer = GroupStudyQuizAttemptResultSerializer(result_attempt)
        return Response(serializer.data)


class GroupStudyQuizAttemptResultView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    serializer_class = GroupStudyQuizAttemptResultSerializer
    lookup_url_kwarg = 'attempt_id'

    def get_queryset(self):
        return get_attempt_result_queryset(self.request.user).filter(
            user=self.request.user,
            is_completed=True,
        )


# ── Leaderboard views ────────────────────────────────────────────────────────

def _leaderboard_user_payload(user):
    return {
        'id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
    }


class StudyGroupLeaderboardView(APIView):
    """Members ranked by the sum of their best score in each group quiz."""

    permission_classes = [IsAuthenticatedUserOnly]

    def get(self, request, group_id):
        group = get_group_object(group_id)
        ensure_member(group, request.user)

        best_rows = (
            GroupStudyQuizAttempt.objects.filter(
                quiz__group=group,
                is_completed=True,
            )
            .values('user_id', 'quiz_id')
            .annotate(best_score=Max('score'))
        )

        totals = {}
        for row in best_rows:
            entry = totals.setdefault(
                row['user_id'],
                {'score': Decimal('0'), 'quizzes_attempted': 0},
            )
            entry['score'] += row['best_score'] or Decimal('0')
            entry['quizzes_attempted'] += 1

        users = {
            user.id: user
            for user in get_user_model().objects.filter(
                id__in=list(totals.keys()),
            )
        }
        ordered = sorted(
            totals.items(),
            key=lambda item: (
                -item[1]['score'],
                -item[1]['quizzes_attempted'],
                users[item[0]].username if item[0] in users else '',
            ),
        )

        results = []
        for index, (user_id, entry) in enumerate(ordered, start=1):
            user = users.get(user_id)
            if user is None:
                continue
            results.append({
                'rank': index,
                'user': _leaderboard_user_payload(user),
                'score': decimal_as_number(entry['score']),
                'quizzes_attempted': entry['quizzes_attempted'],
                'completed_at': None,
                'is_current_user': user_id == request.user.id,
            })
        return Response(results)


class GroupStudyQuizLeaderboardView(APIView):
    """Best completed attempt per member for a single quiz."""

    permission_classes = [IsAuthenticatedUserOnly]

    def get(self, request, quiz_id):
        quiz = get_object_or_404(
            GroupStudyQuiz.objects.select_related('group'),
            pk=quiz_id,
        )
        ensure_member(quiz.group, request.user)

        rows = (
            GroupStudyQuizAttempt.objects.filter(
                quiz=quiz,
                is_completed=True,
            )
            .values('user_id')
            .annotate(best_score=Max('score'), completed_at=Min('end_time'))
            .order_by('-best_score', 'completed_at', 'user_id')
        )

        user_ids = [row['user_id'] for row in rows]
        users = {
            user.id: user
            for user in get_user_model().objects.filter(id__in=user_ids)
        }

        results = []
        for index, row in enumerate(rows, start=1):
            user = users.get(row['user_id'])
            if user is None:
                continue
            completed_at = row['completed_at']
            results.append({
                'rank': index,
                'user': _leaderboard_user_payload(user),
                'score': decimal_as_number(row['best_score']),
                'quizzes_attempted': 1,
                'completed_at': completed_at.isoformat() if completed_at else None,
                'is_current_user': row['user_id'] == request.user.id,
            })
        return Response(results)
