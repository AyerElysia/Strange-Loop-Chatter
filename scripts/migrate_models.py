"""数据迁移脚本

将旧表结构的数据迁移到新表结构。

运行方式：
    python -m scripts.migrate_models
"""

import asyncio
from pathlib import Path

from sqlalchemy import text

# 添加项目根目录到路径
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.kernel.db import get_db_session, configure_engine
from src.kernel.config import ConfigBase, SectionBase, config_section, Field
from src.kernel.logger import get_logger

logger = get_logger("migration", display="Migration")


@config_section("database")
class DatabaseSection(SectionBase):
    """数据库配置节"""

    database_type: str = Field(default="sqlite", description="数据库类型")
    url: str = Field(default="", description="数据库连接 URL")


class MigrationConfig(ConfigBase):
    """迁移配置"""

    database: DatabaseSection = Field(default_factory=DatabaseSection)


async def migrate_person_info():
    """迁移用户信息

    改动：
    - 移除字段：person_name, name_reason, know_times, know_since
    - 新增字段：first_interaction, interaction_count, created_at, updated_at
    - 重命名字段：last_know -> last_interaction
    """
    async with get_db_session() as session:
        # 1. 重命名字段，添加新字段
        await session.execute(
            text("""
                UPDATE person_info
                SET
                    first_interaction = know_since,
                    interaction_count = COALESCE(know_times, 0),
                    created_at = know_since,
                    updated_at = last_know
                WHERE first_interaction IS NULL
            """)
        )

        await session.commit()
        logger.info("PersonInfo 迁移完成：添加新字段")


async def migrate_chat_streams():
    """迁移聊天流

    改动：
    - 移除字段：user_id, user_nickname, user_cardname
    - 新增字段：person_id, chat_type, context_window_size
    - 重命名字段：create_time -> created_at
    """
    async with get_db_session() as session:
        # 1. 生成 person_id
        await session.execute(
            text("""
                UPDATE chat_streams
                SET person_id = platform || ':' || user_id
                WHERE person_id IS NULL
            """)
        )

        # 2. 添加默认值
        await session.execute(
            text("""
                UPDATE chat_streams
                SET
                    chat_type = 'private',
                    context_window_size = 50
                WHERE chat_type IS NULL
            """)
        )

        # 3. 重命名字段
        await session.execute(
            text("""
                UPDATE chat_streams
                SET created_at = create_time
                WHERE created_at IS NULL
            """)
        )

        await session.commit()
        logger.info("ChatStreams 迁移完成：添加 person_id 和默认值")


async def migrate_messages():
    """迁移消息

    改动：
    - 移除字段：user_id, user_nickname, user_cardname
    - 新增字段：person_id, sequence_number, message_type, content, expires_at
    """
    async with get_db_session() as session:
        # 1. 生成 person_id
        await session.execute(
            text("""
                UPDATE messages
                SET person_id = platform || ':' || user_id
                WHERE person_id IS NULL AND user_id IS NOT NULL
            """)
        )

        # 2. 添加 sequence_number（按时间排序）
        await session.execute(
            text("""
                WITH ranked AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY stream_id
                            ORDER BY time ASC
                        ) as seq
                    FROM messages
                    WHERE sequence_number IS NULL
                )
                UPDATE messages
                SET sequence_number = ranked.seq
                FROM ranked
                WHERE messages.id = ranked.id
            """)
        )

        # 3. 设置默认消息类型（如果需要）
        # 注意：content 字段需要根据实际业务逻辑设置
        await session.execute(
            text("""
                UPDATE messages
                SET message_type = 'text'
                WHERE message_type IS NULL OR message_type = ''
            """)
        )

        await session.commit()
        logger.info("Messages 迁移完成：添加 person_id 和 sequence_number")


async def migrate_action_records():
    """迁移动作记录

    改动：
    - 移除字段：chat_info_stream_id, chat_info_platform
    - 重命名字段：chat_id -> stream_id
    - 新增字段：person_id
    """
    async with get_db_session() as session:
        # 1. 重命名字段
        await session.execute(
            text("""
                UPDATE action_records
                SET stream_id = chat_id
                WHERE chat_id IS NOT NULL AND stream_id IS NULL
            """)
        )

        # 2. 添加 person_id（需要通过 stream_id 查找）
        # 注意：这里需要根据实际数据结构进行调整
        # await session.execute(
        #     text("""
        #         UPDATE action_records ar
        #         SET person_id = (
        #             SELECT person_id FROM chat_streams
        #             WHERE stream_id = ar.stream_id
        #         )
        #         WHERE person_id IS NULL
        #     """)
        # )

        await session.commit()
        logger.info("ActionRecords 迁移完成：重命名字段")


async def verify_migration():
    """验证迁移结果"""
    async with get_db_session() as session:
        # 1. 检查 PersonInfo
        result = await session.execute(
            text("""
                SELECT COUNT(*) as count, COUNT(first_interaction) as with_first
                FROM person_info
            """)
        )
        row = result.fetchone()
        logger.info(
            f"PersonInfo: 总数={row[0]}, 有first_interaction={row[1]}"
        )

        # 2. 检查 ChatStreams
        result = await session.execute(
            text("""
                SELECT COUNT(*) as count, COUNT(person_id) as with_person
                FROM chat_streams
            """)
        )
        row = result.fetchone()
        logger.info(
            f"ChatStreams: 总数={row[0]}, 有person_id={row[1]}"
        )

        # 3. 检查 Messages
        result = await session.execute(
            text("""
                SELECT COUNT(*) as count,
                       COUNT(person_id) as with_person,
                       COUNT(sequence_number) as with_seq
                FROM messages
            """)
        )
        row = result.fetchone()
        logger.info(
            f"Messages: 总数={row[0]}, 有person_id={row[1]}, 有sequence_number={row[2]}"
        )

        # 4. 检查 ActionRecords
        result = await session.execute(
            text("""
                SELECT COUNT(*) as count, COUNT(stream_id) as with_stream
                FROM action_records
            """)
        )
        row = result.fetchone()
        logger.info(
            f"ActionRecords: 总数={row[0]}, 有stream_id={row[1]}"
        )


async def run_all_migrations():
    """运行所有迁移"""
    logger.info("=" * 60)
    logger.info("开始数据迁移...")
    logger.info("=" * 60)

    try:
        # 1. 迁移用户信息
        logger.info("\n[1/4] 迁移 PersonInfo...")
        await migrate_person_info()

        # 2. 迁移聊天流
        logger.info("\n[2/4] 迁移 ChatStreams...")
        await migrate_chat_streams()

        # 3. 迁移消息
        logger.info("\n[3/4] 迁移 Messages...")
        await migrate_messages()

        # 4. 迁移动作记录
        logger.info("\n[4/4] 迁移 ActionRecords...")
        await migrate_action_records()

        # 5. 验证迁移结果
        logger.info("\n" + "=" * 60)
        logger.info("验证迁移结果...")
        logger.info("=" * 60)
        await verify_migration()

        logger.info("\n" + "=" * 60)
        logger.info("✅ 数据迁移完成！")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"\n❌ 数据迁移失败：{e}", exc_info=True)
        raise


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="Neo-MoFox 数据库迁移工具")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="配置文件路径（可选）",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="数据库连接 URL（可选，覆盖配置文件）",
    )

    args = parser.parse_args()

    # 加载配置
    if args.config:
        config = MigrationConfig.load(args.config)
    else:
        config = MigrationConfig()

    # 使用命令行参数覆盖配置
    if args.db_url:
        config.database.url = args.db_url

    # 配置数据库引擎
    if config.database.url:
        configure_engine(config.database.url)
    else:
        # 使用默认配置
        configure_engine()

    # 运行迁移
    asyncio.run(run_all_migrations())


if __name__ == "__main__":
    main()
