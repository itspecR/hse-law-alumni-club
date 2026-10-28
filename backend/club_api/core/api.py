import inspect
import re

from django.http import HttpRequest, JsonResponse
from ninja import NinjaAPI
from ninja.errors import ValidationError
from ninja.parser import Parser

from club_api.core.errors import ApiError
from club_api.core.views import json_body


class JsonParser(Parser):
    def parse_body(self, request):
        return json_body(request)


def controller_view(function, parameters):
    body = function.body_model

    async def view(request, **kwargs):
        if body:
            request.validated_body_model = body
            request.validated_body = kwargs.pop("payload")
        return await function(request, **kwargs)

    signature = [inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=HttpRequest)]
    signature += [inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=str) for name in parameters]
    if body:
        signature.append(inspect.Parameter("payload", inspect.Parameter.KEYWORD_ONLY, annotation=body))
    view.__signature__ = inspect.Signature(signature)
    view.__name__ = function.__name__
    return view


def build_api(routes):
    api = NinjaAPI(
        title="Клуб выпускников",
        version="1",
        urls_namespace="club-api",
        docs_url=None,
        openapi_url=None,
        parser=JsonParser(),
    )
    api.add_exception_handler(
        ApiError, lambda request, error: JsonResponse({"error": error.message}, status=error.status)
    )
    api.add_exception_handler(
        ValidationError, lambda request, error: JsonResponse({"error": "Проверьте данные в форме."}, status=400)
    )
    for route in routes:
        pattern = re.sub(r"<str:(\w+)>", r"{\1}", str(route.pattern))
        parameters = re.findall(r"{(\w+)}", pattern)
        for method, function in route.callback.handlers.items():
            view = controller_view(function, parameters)
            name = method.lower() + "_" + re.sub(r"\W", "_", pattern)
            api.default_router.add_api_operation(pattern, [method], view, auth=function.permission, operation_id=name)
            if method == "GET":
                api.default_router.add_api_operation(
                    pattern, ["HEAD"], view, auth=function.permission, operation_id="head_" + name
                )
    return api
