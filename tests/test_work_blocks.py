from app.work_blocks import (
    create_work_block,
    infer_domain,
    match_work_block,
    suggest_block_title,
)


def test_china_milling_task_goes_to_supply_block() -> None:
    text = "подготовить список фрез для заказа в Китае"

    block = create_work_block(123, text)

    assert infer_domain(text) == "supply"
    assert block.title == "Поставки / Китай"
    assert block.domain == "supply"
    assert "китай" in block.aliases
    assert "фрез" in block.entities


def test_astrum_contract_goes_to_contract_block() -> None:
    text = "внести правки в договор с Аструмом"

    block = create_work_block(123, text)

    assert infer_domain(text) == "contracts"
    assert block.title == "Договоры / Аструм"
    assert block.domain == "contracts"
    assert "аструм" in block.entities


def test_related_china_task_reuses_existing_block() -> None:
    existing = create_work_block(123, "подготовить список фрез для заказа в Китае")

    match = match_work_block("вопросы поставщику по фрезам", [existing])

    assert match.block == existing
    assert match.score >= 4
    assert "entity" in match.reason


def test_unknown_task_creates_generic_work_block() -> None:
    block = create_work_block(123, "разобрать странную штуку без контекста")

    assert block.title == "Рабочий блок"
    assert block.domain == "unknown"
    assert block.aliases == ()
    assert block.entities == ()


def test_independent_contract_task_does_not_match_supply_block() -> None:
    supply = create_work_block(123, "подготовить список фрез для заказа в Китае")

    match = match_work_block("внести правки в договор с Аструмом", [supply])

    assert match.block is None


def test_supply_china_and_korea_title() -> None:
    assert suggest_block_title("стикеры по Циркону для Китая и Кореи", "supply") == (
        "Поставки / Китай и Корея"
    )
