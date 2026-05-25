from rest_framework.pagination import PageNumberPagination


class DatasetPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200


class DatasetRowPagination(PageNumberPagination):
    """Higher ceiling for row pagination — users routinely scroll through
    several hundred rows at a time in the editable grid."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 1000
