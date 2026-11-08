from pydantic import BaseModel, ConfigDict

SAFE_INTEGER_MAX = 9007199254740991


class Body(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
