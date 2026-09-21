from rest_framework.pagination import PageNumberPagination


class AdmissionLifePagination(PageNumberPagination):
    """
    Default pagination for AdmissionLife list endpoints.
    """
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100


class LeaderboardPagination(PageNumberPagination):
    """
    Pagination for leaderboard endpoints with a smaller default page size.
    """
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
