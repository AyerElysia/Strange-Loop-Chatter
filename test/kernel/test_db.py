"""
DB 模块简化测试
"""

import pytest
from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, declarative_base

from src.kernel.db import (
    CRUDBase,
    QueryBuilder,
    get_engine,
)

# 创建测试用的 Base 和模型
TestBase = declarative_base()


class TestUser(TestBase):
    """测试用户模型"""
    __tablename__ = "test_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


@pytest.mark.asyncio
async def test_crud_create():
    """测试创建记录"""
    # 创建表
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user = await crud.create({
            "name": "TestUser",
            "age": 25,
            "is_active": True
        })
        assert user.name == "TestUser"
        assert user.age == 25
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter():
    """测试查询过滤"""
    # 创建表并插入数据
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
        ])

        qb = QueryBuilder(TestUser)
        users = await qb.filter(name="Alice").all()
        assert len(users) == 1
        assert users[0].name == "Alice"
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))

@pytest.mark.asyncio
async def test_crud_get():
    """测试根据 ID 获取记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        created = await crud.create({"name": "David", "age": 28, "is_active": True})

        user = await crud.get(created.id)
        assert user is not None
        assert user.name == "David"
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_get_by():
    """测试根据条件获取记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.create({"name": "Eve", "age": 30, "is_active": True})

        user = await crud.get_by(name="Eve")
        assert user is not None
        assert user.age == 30
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_get_multi():
    """测试获取多条记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Frank", "age": 25, "is_active": True},
            {"name": "Grace", "age": 27, "is_active": True},
            {"name": "Henry", "age": 29, "is_active": True},
        ])

        users = await crud.get_multi(skip=0, limit=2)
        assert len(users) == 2
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_update():
    """测试更新记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user = await crud.create({"name": "Ivy", "age": 26, "is_active": True})

        updated = await crud.update(user.id, {"age": 27, "email": "ivy@test.com"})
        assert updated is not None
        assert updated.age == 27
        assert updated.email == "ivy@test.com"
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_delete():
    """测试删除记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user = await crud.create({"name": "Jack", "age": 32, "is_active": True})

        success = await crud.delete(user.id)
        assert success is True

        deleted_user = await crud.get(user.id)
        assert deleted_user is None
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_count():
    """测试统计记录数"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        initial_count = await crud.count()

        await crud.bulk_create([
            {"name": "Kate", "age": 24, "is_active": True},
            {"name": "Leo", "age": 26, "is_active": True},
        ])

        new_count = await crud.count()
        assert new_count == initial_count + 2
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_exists():
    """测试检查记录是否存在"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        assert not await crud.exists(name="NonExistent")

        await crud.create({"name": "Mary", "age": 28, "is_active": True})
        assert await crud.exists(name="Mary")
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_crud_get_or_create():
    """测试获取或创建记录"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        user1, created1 = await crud.get_or_create(
            defaults={"age": 30},
            name="Nancy"
        )
        assert created1 is True
        assert user1.name == "Nancy"

        user2, created2 = await crud.get_or_create(
            defaults={"age": 35},
            name="Nancy"
        )
        assert created2 is False
        assert user2.id == user1.id
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter_operators():
    """测试查询过滤操作符"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": False},
        ])

        # 测试 gt
        qb1 = QueryBuilder(TestUser)
        users = await qb1.filter(age__gt=28).all()
        assert len(users) == 2

        # 测试 lt
        qb2 = QueryBuilder(TestUser)
        users = await qb2.filter(age__lt=30).all()
        assert len(users) == 1

        # 测试 in
        qb3 = QueryBuilder(TestUser)
        users = await qb3.filter(age__in=[25, 35]).all()
        assert len(users) == 2

        # 测试 like
        qb4 = QueryBuilder(TestUser)
        users = await qb4.filter(name__like="%a%").all()
        assert len(users) == 2
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_order_and_pagination():
    """测试排序和分页"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "User1", "age": 20, "is_active": True},
            {"name": "User2", "age": 25, "is_active": True},
            {"name": "User3", "age": 30, "is_active": True},
        ])

        # 测试升序排序
        qb1 = QueryBuilder(TestUser)
        users = await qb1.order_by("age").all()
        assert users[0].age == 20

        # 测试降序排序
        qb2 = QueryBuilder(TestUser)
        users = await qb2.order_by("-age").all()
        assert users[0].age == 30

        # 测试分页
        qb3 = QueryBuilder(TestUser)
        items, total = await qb3.paginate(page=1, page_size=2)
        assert total == 3
        assert len(items) == 2
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_more_operators():
    """测试更多查询操作符"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "email": "alice@test.com", "is_active": True},
            {"name": "Bob", "age": 30, "email": None, "is_active": True},
            {"name": "Charlie", "age": 35, "email": "charlie@test.com", "is_active": False},
        ])

        # 测试 gte
        qb1 = QueryBuilder(TestUser)
        users = await qb1.filter(age__gte=30).all()
        assert len(users) == 2

        # 测试 lte
        qb2 = QueryBuilder(TestUser)
        users = await qb2.filter(age__lte=30).all()
        assert len(users) == 2

        # 测试 ne
        qb3 = QueryBuilder(TestUser)
        users = await qb3.filter(name__ne="Alice").all()
        assert len(users) == 2

        # 测试 nin
        qb4 = QueryBuilder(TestUser)
        users = await qb4.filter(age__nin=[25, 35]).all()
        assert len(users) == 1
        assert users[0].name == "Bob"

        # 测试 isnull
        qb5 = QueryBuilder(TestUser)
        users = await qb5.filter(email__isnull=True).all()
        assert len(users) == 1

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_first_count_exists():
    """测试 first, count, exists 方法"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
        ])

        # first
        qb1 = QueryBuilder(TestUser)
        user = await qb1.filter(name="Alice").first()
        assert user is not None
        assert user.name == "Alice"

        # first not found
        qb2 = QueryBuilder(TestUser)
        user = await qb2.filter(name="NonExistent").first()
        assert user is None

        # count
        qb3 = QueryBuilder(TestUser)
        count = await qb3.filter(is_active=True).count()
        assert count == 2

        # exists
        qb4 = QueryBuilder(TestUser)
        assert await qb4.filter(name="Alice").exists()

        # not exists
        qb5 = QueryBuilder(TestUser)
        assert not await qb5.filter(name="NonExistent").exists()

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_bulk_operations():
    """测试批量操作"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)

        # bulk_create
        users = await crud.bulk_create([
            {"name": "Rose", "age": 25, "is_active": True},
            {"name": "Sam", "age": 26, "is_active": True},
            {"name": "Tom", "age": 27, "is_active": True},
        ])
        assert len(users) == 3

        # bulk_update
        updates = [
            (users[0].id, {"age": 26}),
            (users[1].id, {"age": 27}),
        ]
        count = await crud.bulk_update(updates)
        assert count == 2

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_filter_or():
    """测试 OR 过滤"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": False},
        ])

        # filter_or - 不同字段的 OR 条件
        qb = QueryBuilder(TestUser)
        users = await qb.filter_or(name="Alice", age=35).all()
        assert len(users) == 2

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_edge_cases():
    """测试边界情况"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)

        # get 不存在的记录
        user = await crud.get(99999)
        assert user is None

        # get_by 不存在的记录
        user = await crud.get_by(name="NonExistent")
        assert user is None

        # update 不存在的记录
        result = await crud.update(99999, {"age": 30})
        assert result is None

        # delete 不存在的记录
        success = await crud.delete(99999)
        assert success is False

        # bulk_update 空列表
        count = await crud.bulk_update([])
        assert count == 0

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_aggregate_query():
    """测试聚合查询"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.query import AggregateQuery
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": "Alice", "age": 25, "is_active": True},
            {"name": "Bob", "age": 30, "is_active": True},
            {"name": "Charlie", "age": 35, "is_active": False},
        ])

        agg = AggregateQuery(TestUser)

        # sum
        total_age = await agg.sum("age")
        assert total_age == 90

        # avg
        avg_age = await agg.avg("age")
        assert avg_age == 30

        # max
        max_age = await agg.max("age")
        assert max_age == 35

        # min
        min_age = await agg.min("age")
        assert min_age == 25

        # with filter
        agg2 = AggregateQuery(TestUser)
        agg2.filter(is_active=True)
        total_active_age = await agg2.sum("age")
        assert total_active_age == 55

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_iterators():
    """测试迭代器方法"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        crud = CRUDBase(TestUser)
        await crud.bulk_create([
            {"name": f"User{i}", "age": 20 + i, "is_active": True}
            for i in range(10)
        ])

        qb = QueryBuilder(TestUser)

        # iter_batches
        batch_count = 0
        async for batch in qb.iter_batches(batch_size=3):
            batch_count += 1
            assert len(batch) <= 3
        assert batch_count == 4  # 10条数据，每批3条，需要4批

        # iter_all
        count = 0
        async for user in qb.iter_all():
            count += 1
        assert count == 10

        # iter_all as_dict=False
        count = 0
        async for user in qb.iter_all(as_dict=False):
            count += 1
            assert hasattr(user, "name")
        assert count == 10

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_query_empty_result():
    """测试空结果情况"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        qb = QueryBuilder(TestUser)

        # all on empty table
        users = await qb.all()
        assert len(users) == 0

        # first on empty table
        user = await qb.first()
        assert user is None

        # count on empty table
        count = await qb.count()
        assert count == 0

        # exists on empty table
        exists = await qb.exists()
        assert exists is False

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))


@pytest.mark.asyncio
async def test_model_to_dict_edge_cases():
    """测试模型转换的边界情况"""
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TestBase.metadata.create_all(sync_conn))

    try:
        from src.kernel.db.api.crud import _model_to_dict, _dict_to_model

        # 测试 _model_to_dict with None
        result = _model_to_dict(None)
        assert result == {}

        # 测试 _dict_to_model
        user_dict = {"name": "Test", "age": 25, "is_active": True}
        user = _dict_to_model(TestUser, user_dict)
        assert user.name == "Test"
        assert user.age == 25

    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: TestBase.metadata.drop_all(sync_conn))
