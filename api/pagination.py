# api/pagination.py (Create this new file)

from rest_framework.pagination import PageNumberPagination

class StandardResultsSetPagination(PageNumberPagination):
    """
    Custom pagination class to set a default page size of 50.
    """
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100
