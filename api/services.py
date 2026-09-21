import random

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import Category, Question, Quiz, QuizAttempt


def get_effective_category_ids(category, include_subcategories):
    """Return the selected category and, when requested, every descendant."""
    category_ids = {category.pk}
    if not include_subcategories:
        return category_ids

    pending_ids = [category.pk]
    while pending_ids:
        child_ids = set(
            Category.objects.filter(parent_id__in=pending_ids)
            .values_list('id', flat=True)
        ) - category_ids
        if not child_ids:
            break
        category_ids.update(child_ids)
        pending_ids = list(child_ids)
    return category_ids


def _configuration_value(configuration, key, fallback=None):
    if isinstance(configuration, dict):
        if key in configuration:
            return configuration[key]
        return configuration.get(fallback) if fallback else None
    if hasattr(configuration, key):
        return getattr(configuration, key)
    return getattr(configuration, fallback) if fallback else None


def select_question_ids(configurations, *, strict=True):
    selected_question_ids = set()
    used_category_ids = set()

    for index, configuration in enumerate(configurations):
        category = _configuration_value(configuration, 'category')
        count = _configuration_value(configuration, 'count', 'question_count')
        include_subcategories = bool(
            _configuration_value(configuration, 'include_subcategories')
        )

        if not isinstance(category, Category):
            raise ValidationError({
                'config': {str(index): ['A valid category is required.']},
            })
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValidationError({
                'config': {str(index): ['Question count must be a positive integer.']},
            })

        effective_category_ids = get_effective_category_ids(
            category,
            include_subcategories,
        )
        overlap = used_category_ids & effective_category_ids
        if strict and overlap:
            overlapping_paths = [
                item.get_full_path()
                for item in Category.objects.filter(pk__in=overlap)
            ]
            raise ValidationError({
                'config': {
                    str(index): [
                        'Effective category scope overlaps an earlier row: '
                        + ', '.join(sorted(overlapping_paths))
                    ],
                },
            })
        used_category_ids.update(effective_category_ids)

        available_question_ids = list(
            Question.objects.filter(category_id__in=effective_category_ids)
            .values_list('id', flat=True)
        )
        if strict:
            available_question_ids = [
                question_id
                for question_id in available_question_ids
                if question_id not in selected_question_ids
            ]

        if strict and len(available_question_ids) < count:
            raise ValidationError({
                'config': {
                    str(index): [
                        f"Category '{category.get_full_path()}' requested {count} "
                        f"questions, but only {len(available_question_ids)} unique "
                        'questions are available.'
                    ],
                },
            })

        if len(available_question_ids) <= count:
            selected_question_ids.update(available_question_ids)
        else:
            selected_question_ids.update(random.sample(available_question_ids, count))

    if not selected_question_ids:
        raise ValidationError({
            'config': ['At least one category configuration is required.'],
        })
    return selected_question_ids


@transaction.atomic
def generate_quiz(
    *,
    configurations,
    name,
    duration_minutes,
    correct_mark,
    wrong_mark,
    unanswered_mark,
    source_template=None,
    user=None,
    guest_user=None,
    strict=True,
):
    if user is not None and guest_user is not None:
        raise ValidationError({'owner': ['A quiz cannot have both user and guest owners.']})

    question_ids = select_question_ids(configurations, strict=strict)
    quiz = Quiz.objects.create(
        name=name,
        duration_minutes=duration_minutes,
        quiz_type=Quiz.QuizType.MODEL_TEST,
        source_template=source_template,
        correct_mark=correct_mark,
        wrong_mark=wrong_mark,
        unanswered_mark=unanswered_mark,
        generated_for_user=user,
        generated_for_guest=guest_user,
    )
    quiz.questions.set(question_ids)
    return quiz


@transaction.atomic
def start_template(template, *, user=None, guest_user=None):
    if (user is None) == (guest_user is None):
        raise ValidationError({
            'owner': ['Exactly one registered or guest owner is required.'],
        })

    quiz = generate_quiz(
        configurations=list(template.category_configurations.select_related('category')),
        name=template.name,
        duration_minutes=template.duration_minutes,
        correct_mark=template.correct_mark,
        wrong_mark=template.wrong_mark,
        unanswered_mark=template.unanswered_mark,
        source_template=template,
        user=user,
        guest_user=guest_user,
    )
    attempt = QuizAttempt.objects.create(
        user=user,
        guest_user=guest_user,
        quiz=quiz,
    )
    return quiz, attempt
