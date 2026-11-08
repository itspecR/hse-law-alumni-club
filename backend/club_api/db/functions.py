from django.db import models


class DatabaseUUID(models.Func):
    function = "UUID"
    output_field = models.UUIDField()
