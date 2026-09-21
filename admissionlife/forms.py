from django import forms
from .models import Category


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
