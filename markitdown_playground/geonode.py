import requests
from pydantic import BaseModel, ConfigDict, PositiveInt, PositiveFloat, ValidationError


class GeoNode(BaseModel):
    model_config = ConfigDict(strict=True)
    ip: str
    anonymityLevel: str
    asn: str | None
    city: str
    country: str
    created_at: str
    google: bool
    isp: str
    lastChecked: PositiveInt
    latency: PositiveFloat
    org: str | None
    port: str
    protocols: list[str]
    region: str | None
    responseTime: PositiveInt
    speed: PositiveInt
    updated_at: str
    workingPercent: PositiveInt | None
    upTime: PositiveFloat
    upTimeSuccessCount: PositiveInt
    upTimeTryCount: PositiveInt


class GeoNodeResponse(BaseModel):
    data: list[GeoNode]


if __name__ == "__main__":
    res = requests.get("https://proxylist.geonode.com/api/proxy-list?limit=5")
    print(res.json())
    try:
        models = GeoNodeResponse.model_validate(res.json())
        print(models)
        for model in models:
            print(model)
    except ValidationError as e:
        print(e)
