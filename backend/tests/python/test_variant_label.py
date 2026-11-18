from types import SimpleNamespace

from club_api.modules.catalog.lookup import variant_label
from club_api.modules.checkout.orders import order_summary, receipt_line

HOODIE = {
    "variants": [{"sku": "hoodie-graphite-M", "size": "M", "color": "графит"}, {"sku": "bag", "color": "костяной"}]
}


def test_variant_label_names_size_and_color():
    assert variant_label(HOODIE, "hoodie-graphite-M") == "Размер M, графит"
    assert variant_label(HOODIE, "bag") == "костяной"
    assert variant_label(HOODIE, "missing") == ""
    assert variant_label(None, "hoodie-graphite-M") == ""
    assert variant_label(HOODIE, None) == ""


def test_order_summary_prefers_variant_words_over_sku():
    priced = [
        {"title": "Худи", "variant_sku": "hoodie-graphite-M", "variant_label": "Размер M, графит", "qty": 1},
        {"title": "Шопер", "variant_sku": "shopper-kost", "qty": 2},
        {"title": "Кружка", "qty": 1},
    ]
    assert order_summary(priced) == "Худи (Размер M, графит) ×1; Шопер (shopper-kost) ×2; Кружка ×1"


def test_order_letter_names_how_merch_is_received():
    merch = [{"type": "merch"}]
    assert receipt_line(SimpleNamespace(fulfillment="pickup", address=None), merch) == "Получение: самовывоз.\n"
    delivery = SimpleNamespace(fulfillment="delivery", address=" Москва, ул. Примерная, 1 ")
    assert receipt_line(delivery, merch) == "Получение: доставка по адресу Москва, ул. Примерная, 1.\n"
    assert receipt_line(delivery, [{"type": "dpo"}]) == ""
