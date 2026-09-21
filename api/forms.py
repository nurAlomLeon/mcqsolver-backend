from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import Category, ModelTestTemplate, ModelTestTemplateCategory, Question
from .services import get_effective_category_ids


class CsvImportForm(forms.Form):
    csv_file = forms.FileField()
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        empty_label="-- Use category from CSV --",
        help_text="Select a category to assign to all imported questions. Leave blank to use the category specified in the CSV file."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Display full path for hierarchical categories
        self.fields['category'].label_from_instance = lambda obj: obj.get_full_path()


class ModelTestTemplateForm(forms.ModelForm):
    class Meta:
        model = ModelTestTemplate
        fields = '__all__'


class ModelTestTemplateCategoryForm(forms.ModelForm):
    class Meta:
        model = ModelTestTemplateCategory
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].label_from_instance = lambda obj: obj.get_full_path()


class ModelTestTemplateCategoryInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        used_category_ids = set()
        configuration_count = 0
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or form.cleaned_data.get('DELETE'):
                continue
            category = form.cleaned_data.get('category')
            if category is None:
                continue
            configuration_count += 1
            effective_ids = get_effective_category_ids(
                category,
                form.cleaned_data.get('include_subcategories', True),
            )
            if used_category_ids & effective_ids:
                raise ValidationError(
                    'Category rows must not have duplicate or overlapping effective scopes.'
                )
            used_category_ids.update(effective_ids)

            question_count = form.cleaned_data.get('question_count')
            available_count = Question.objects.filter(
                category_id__in=effective_ids,
            ).count()
            if question_count and question_count > available_count:
                form.add_error(
                    'question_count',
                    f'Only {available_count} questions are currently available in this scope.',
                )

        if configuration_count == 0:
            raise ValidationError('Add at least one category configuration.')
