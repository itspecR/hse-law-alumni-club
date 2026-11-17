class ApiError(Exception):
    def __init__(self, status: int, message: str, field: str | None = None):
        self.status = status
        self.message = message
        self.field = field
        super().__init__(message)

    def payload(self):
        return {"error": self.message} | ({"field": self.field} if self.field else {})
