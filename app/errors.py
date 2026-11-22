class BusinessError(Exception):
    """A rule of the business was violated; the message is safe to show the user."""


class StockError(BusinessError):
    pass
