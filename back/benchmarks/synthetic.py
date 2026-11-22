"""
Synthetic Russian text/DOCX generators shared by the benchmarks.

No real PII: names, phones, addresses are fabricated patterns.
Deterministic under a fixed seed so runs are comparable.
"""

import io
import random
from dataclasses import dataclass

from docx import Document

random.seed(42)

SURNAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Соколов", "Попов", "Козлов"]
GIVEN = ["Иван", "Пётр", "Анна", "Мария", "Сергей", "Ольга", "Дмитрий"]
PATRONYMIC = ["Иванович", "Петрович", "Сергеевна", "Андреевна", "Дмитриевна", "Александрович"]
ORGS = ["Росхим", "Строймонтаж", "Газпром", "Транснефть", "Сбербанк", "Вектор"]
CITIES = ["Москва", "Санкт-Петербург", "Казань", "Новосибирск", "Екатеринбург"]
STREETS = ["ул. Ленина", "просп. Мира", "ул. Гагарина", "б-р Победы", "ул. Строителей"]


def sentence(i: int) -> str:
    """One contract-style sentence packed with PII of every label."""
    name = f"{random.choice(GIVEN)} {random.choice(PATRONYMIC)} {random.choice(SURNAMES)}"
    phone = f"+7 ({random.randint(300, 999)}) {random.randint(100, 999)}-{random.randint(1000, 9999)}"
    org = f"ООО «{random.choice(ORGS)}-{i % 7}»"
    addr = f"{random.choice(CITIES)}, {random.choice(STREETS)}, д. {random.randint(1, 99)}"
    return (
        f"Договор №{i}/2026 заключён между {org} и {name}. "
        f"Контактный телефон: {phone}. Адрес: {addr}. "
        f"Электронная почта: user{i}@example.com. "
        f"Подробнее: https://example.com/docs/contract-{i}. "
    )


def build_text(n_sentences: int) -> str:
    return "".join(sentence(i) for i in range(n_sentences))


@dataclass(slots=True)
class DocxShape:
    paragraphs: int
    table_rows: int = 0
    nested: bool = False


def person_line(i: int) -> str:
    return (
        f"Подписант {i}: {random.choice(GIVEN)} {random.choice(PATRONYMIC)} "
        f"{random.choice(SURNAMES)}, телефон +7 (900) 123-45-{i % 100:02d}. "
    )


def build_docx(shape: DocxShape) -> bytes:
    doc = Document()
    for i in range(shape.paragraphs):
        doc.add_paragraph(person_line(i))
    if shape.table_rows:
        table = doc.add_table(rows=shape.table_rows, cols=2)
        for r in range(shape.table_rows):
            table.cell(r, 0).text = person_line(10_000 + r)
            table.cell(r, 1).text = f"ООО «Тест-{r}», ИНН {1234567890 + r}"
        if shape.nested:
            inner = table.cell(0, 0).add_table(rows=2, cols=1)
            inner.cell(0, 0).text = person_line(20_001)
            inner.cell(1, 0).text = person_line(20_002)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
