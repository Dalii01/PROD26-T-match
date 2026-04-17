import asyncio
import csv
import random
import string
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.models.interaction import Interaction
from app.models.transaction import Transaction
from app.models.user import User, UserFeatures, UserPhoto

MALE_FIRST_NAMES = [
    "Алексей",
    "Иван",
    "Никита",
    "Павел",
    "Тимур",
    "Егор",
    "Максим",
    "Денис",
    "Кирилл",
    "Артем",
]

FEMALE_FIRST_NAMES = [
    "Дарья",
    "Мария",
    "Ольга",
    "София",
    "Юлия",
    "Алина",
    "Анастасия",
    "Елена",
    "Полина",
    "Валерия",
]

MALE_LAST_NAMES = [
    "Иванов",
    "Смирнов",
    "Соколов",
    "Попов",
    "Морозов",
    "Орлов",
    "Николаев",
    "Соловьев",
    "Степанов",
    "Зайцев",
]

FEMALE_LAST_NAMES = [
    "Петрова",
    "Кузнецова",
    "Новикова",
    "Лебедева",
    "Волкова",
    "Михайлова",
    "Федорова",
    "Гусева",
    "Виноградова",
    "Белова",
]

CITIES = [
    "Москва",
    "Санкт-Петербург",
    "Казань",
    "Нижний Новгород",
    "Екатеринбург",
    "Новосибирск",
    "Самара",
    "Краснодар",
    "Воронеж",
    "Уфа",
]


def random_birth_date() -> date:
    # Возраст от 14 до 36 лет (для 2026 года)
    year = random.randint(1990, 2012)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return date(year, month, day)


def random_features() -> dict:
    vector = [round(random.random(), 4) for _ in range(8)]
    tags = random.sample(
        [
            "кофе",
            "путешествия",
            "спорт",
            "кино",
            "книги",
            "музыка",
            "рестораны",
            "игры",
            "природа",
            "искусство",
        ],
        k=3,
    )
    return {"vector": vector, "tags": tags}


def _photo_url(gender: str, index: int) -> str:
    # RandomUser provides real human photos; using direct stable URLs
    bucket = "men" if gender == "male" else "women"
    return f"https://randomuser.me/api/portraits/{bucket}/{index}.jpg"


def _hash_to_int(value: str) -> int:
    return abs(hash(value)) % (2**63 - 1)


def _load_transactions_rows() -> list[dict[str, str]]:
    csv_path = Path(__file__).with_name("transaction_600_new.csv")
    rows: list[dict[str, str]] = []
    if not csv_path.exists():
        return rows
    with csv_path.open(newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return rows
        ts_key = next(
            (key for key in reader.fieldnames if "real_transaction_dttm" in key),
            "real_transaction_dttm",
        )
        for row in reader:
            normalized = {
                "real_transaction_dttm": row.get(ts_key, ""),
                "party_rk": row.get("party_rk", ""),
                "transaction_rk": row.get("transaction_rk", ""),
                "merchant_type_code": row.get("merchant_type_code", ""),
                "merchant_nm": row.get("merchant_nm", ""),
                "category_nm": row.get("category_nm", ""),
            }
            rows.append(normalized)
    return rows


def _parse_transaction_datetime(value: str) -> datetime:
    if not value:
        return datetime.utcnow()
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _mutate_merchant_name(name: str, suffix: str) -> str:
    if not name:
        return f"Merchant {suffix}"
    if len(name) > 40:
        return f"{name[:37]}-{suffix}"
    return f"{name} {suffix}"


def _random_suffix() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=4))


def _randomize_datetime(dt: datetime) -> datetime:
    # Shift timestamp within +/- 60 days and randomize time
    shift_days = random.randint(-60, 60)
    new_dt = dt + timedelta(days=shift_days)
    return new_dt.replace(
        hour=random.randint(0, 23),
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
    )


def _build_user_transactions(
    user_party_rk: int,
    base_rows: list[dict[str, str]],
    target_count: int,
    cohort_key: int,
) -> list[Transaction]:
    if not base_rows:
        base_rows = [
            {
                "real_transaction_dttm": datetime.utcnow().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "transaction_rk": f"synthetic-{i}",
                "merchant_type_code": "5999",
                "merchant_nm": "Generic Store",
                "category_nm": "shopping",
            }
            for i in range(50)
        ]

    core_size = min(30, len(base_rows))
    core_rows = random.sample(base_rows, k=core_size)
    transactions: list[Transaction] = []
    used_rks: set[int] = set()

    similar_mode = cohort_key % 3 == 0
    core_share = 0.65 if similar_mode else 0.4
    keep_name_chance = 0.7 if similar_mode else 0.4
    keep_category_chance = 0.8 if similar_mode else 0.5

    attempts = 0
    while len(transactions) < target_count and attempts < target_count * 10:
        attempts += 1
        use_core = random.random() < core_share
        sample = random.choice(core_rows if use_core else base_rows)

        base_dt = _parse_transaction_datetime(sample.get("real_transaction_dttm") or "")
        dt_value = _randomize_datetime(base_dt)

        merchant_nm = sample.get("merchant_nm") or ""
        if merchant_nm and random.random() > keep_name_chance:
            merchant_nm = _mutate_merchant_name(merchant_nm, _random_suffix())

        category_nm = sample.get("category_nm") or None
        if category_nm and random.random() > keep_category_chance:
            category_nm = None

        # Ensure uniqueness per user by salting with user_party_rk and sequence.
        rk_seed = f"{user_party_rk}-{sample.get('transaction_rk','')}-{len(transactions)}-{_random_suffix()}"
        transaction_rk = _hash_to_int(rk_seed)
        if transaction_rk in used_rks:
            continue
        used_rks.add(transaction_rk)

        transactions.append(
            Transaction(
                real_transaction_dttm=dt_value,
                party_rk=user_party_rk,
                transaction_rk=transaction_rk,
                merchant_type_code=sample.get("merchant_type_code") or None,
                merchant_nm=merchant_nm or None,
                category_nm=category_nm,
            )
        )

    return transactions


async def run() -> None:
    async with AsyncSessionLocal() as session:
        existing_rows = await session.execute(select(User.id))
        existing_ids = existing_rows.scalars().all()
        existing_count = len(existing_ids)
        target_count = 100
        to_create = max(0, target_count - existing_count)
        users: list[User] = []
        for idx in range(to_create):
            gender = random.choice(["female", "male"])
            if gender == "female":
                first_name = random.choice(FEMALE_FIRST_NAMES)
                last_name = random.choice(FEMALE_LAST_NAMES)
            else:
                first_name = random.choice(MALE_FIRST_NAMES)
                last_name = random.choice(MALE_LAST_NAMES)
            nickname = f"user{existing_count + idx + 1}"
            user = User(
                external_party_rk=10_000_000 + existing_count + idx,
                first_name=first_name,
                last_name=last_name,
                nickname=nickname,
                bio=(
                    f"Привет, я {first_name}. Мне нравятся "
                    f"{random.choice(['кофе', 'путешествия', 'музыка', 'искусство'])}."
                ),
                gender=gender,
                city=random.choice(CITIES),
                birth_date=random_birth_date(),
                is_active=True,
            )
            users.append(user)
            session.add(user)

        if to_create:
            await session.flush()

        transaction_rows = _load_transactions_rows()

        # Assign transactions to newly created users and to existing users without any transactions.
        all_users_rows = await session.execute(select(User))
        all_users = all_users_rows.scalars().all()
        existing_parties_rows = await session.execute(
            select(Transaction.party_rk).distinct()
        )
        existing_parties = {row[0] for row in existing_parties_rows.all()}
        users_for_transactions = [
            user
            for user in all_users
            if (user.external_party_rk or user.id) not in existing_parties
        ]

        # Ensure user 3 has 10-15 inbound likes to show immediately.
        target_user_id = 3
        if any(user.id == target_user_id for user in all_users):
            existing_likes_rows = await session.execute(
                select(Interaction.actor_id).where(
                    Interaction.target_id == target_user_id,
                    Interaction.action == "like",
                )
            )
            existing_like_actor_ids = {row[0] for row in existing_likes_rows.all()}
            desired_total_likes = random.randint(10, 15)
            missing_likes = max(0, desired_total_likes - len(existing_like_actor_ids))
            if missing_likes:
                eligible_actors = [
                    user
                    for user in all_users
                    if user.id != target_user_id
                    and user.id not in existing_like_actor_ids
                ]
                random.shuffle(eligible_actors)
                for actor in eligible_actors[:missing_likes]:
                    session.add(
                        Interaction(
                            actor_id=actor.id,
                            target_id=target_user_id,
                            action="like",
                        )
                    )

        for idx, user in enumerate(users):
            photo = UserPhoto(
                user_id=user.id,
                url=_photo_url(user.gender or "male", (existing_count + idx) % 100),
                is_primary=True,
            )
            features = UserFeatures(
                user_id=user.id,
                features=random_features(),
            )
            session.add_all([photo, features])

        for idx, user in enumerate(users_for_transactions):
            per_user = random.randint(140, 160)
            party_rk = user.external_party_rk or user.id
            new_transactions = _build_user_transactions(
                user_party_rk=party_rk,
                base_rows=transaction_rows,
                target_count=per_user,
                cohort_key=idx,
            )
            session.add_all(new_transactions)

        await session.commit()
        print(f"Seeded {to_create} users. Total: {target_count}.")
        print(f"Assigned transactions to {len(users_for_transactions)} users.")


if __name__ == "__main__":
    asyncio.run(run())
