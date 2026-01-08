#!/usr/bin/env python3
"""
Test suite for Supabase tools (init, migration, sql_execution) with real K8S pod execution.
"""

import sys
import asyncio
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from tools.miaoda.supabase_init import supabase_init_func
from tools.miaoda.supabase_migration import supabase_migration_func
from tools.miaoda.supabase_sql_execution import supabase_sql_func
from tools.tests.miaoda.test_base import MiaodaToolTestBase


class TestSupabaseInit(MiaodaToolTestBase):
    """Test cases for supabase_init tool."""

    def test_local_simple_init(self):
        """Test init with simple project name."""
        result = supabase_init_func("test_project")

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        assert "initialized successfully" in result["result"], "Result should mention success"
        print("✅ test_local_simple_init: PASSED")

    def test_local_init_with_app_id(self):
        """Test init with app_id."""
        result = supabase_init_func("test_project", "app-123")

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        assert "app-123" in result["result"], "Result should mention app_id"
        print("✅ test_local_init_with_app_id: PASSED")

    def test_local_init_special_chars(self):
        """Test init with special characters in name."""
        names = ["test-project", "test_project_123", "my-app-2024"]

        for name in names:
            result = supabase_init_func(name)
            assert result["status"] == "success", f"Failed for name: {name}"

        print("✅ test_local_init_special_chars: PASSED")

    async def test_k8s_simple_init(self):
        """Test K8S execution for simple init."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_supabase_init",
            parameters={"name": "test_project"},
            test_name="supabase-init-simple"
        )

        self.assert_success(result, "test_k8s_simple_init")

    async def test_k8s_init_with_app_id(self):
        """Test K8S execution for init with app_id."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_supabase_init",
            parameters={"name": "test_project", "app_id": "app-456"},
            test_name="supabase-init-appid"
        )

        self.assert_success(result, "test_k8s_init_with_app_id")


class TestSupabaseMigration(MiaodaToolTestBase):
    """Test cases for supabase_migration tool."""

    def test_local_simple_migration(self):
        """Test migration with simple SQL."""
        result = supabase_migration_func("create_users", "CREATE TABLE users (id INT);")

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        assert "applied successfully" in result["result"], "Result should mention success"
        print("✅ test_local_simple_migration: PASSED")

    def test_local_complex_migration(self):
        """Test migration with complex SQL."""
        sql = """-- Create users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE
);

-- Create index
CREATE INDEX idx_users_email ON users(email);"""

        result = supabase_migration_func("create_users_schema", sql)

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        print("✅ test_local_complex_migration: PASSED")

    def test_local_migration_with_special_chars(self):
        """Test migration with special characters."""
        sqls = [
            "INSERT INTO config VALUES ('key', 'value');",
            "INSERT INTO paths VALUES ('C:\\\\Users\\\\test');",
            "INSERT INTO data VALUES ('It''s a test & demo');",
            "CREATE TABLE test (data TEXT DEFAULT '$VAR');",
        ]

        for sql in sqls:
            result = supabase_migration_func(f"migration_{sqls.index(sql)}", sql)
            assert result["status"] == "success", f"Failed for SQL: {sql[:50]}"

        print("✅ test_local_migration_with_special_chars: PASSED")

    async def test_k8s_simple_migration(self):
        """Test K8S execution for simple migration."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_supabase_migration",
            parameters={"name": "create_table", "query": "CREATE TABLE test (id INT);"},
            test_name="supabase-migration-simple"
        )

        self.assert_success(result, "test_k8s_simple_migration")

    async def test_k8s_complex_migration(self):
        """Test K8S execution for complex migration."""
        sql = """-- Create profiles table
CREATE TABLE IF NOT EXISTS profiles (
    id UUID PRIMARY KEY,
    phone VARCHAR(20) UNIQUE NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'employee',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);"""

        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_supabase_migration",
            parameters={"name": "create_profiles", "query": sql},
            test_name="supabase-migration-complex"
        )

        self.assert_success(result, "test_k8s_complex_migration")


class TestSupabaseSqlExecution(MiaodaToolTestBase):
    """Test cases for supabase_sql_execution tool."""

    def test_local_simple_query(self):
        """Test SQL execution with simple query."""
        result = supabase_sql_func("SELECT * FROM users LIMIT 10;")

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        assert "executed successfully" in result["result"], "Result should mention success"
        print("✅ test_local_simple_query: PASSED")

    def test_local_query_with_app_id(self):
        """Test SQL execution with app_id."""
        result = supabase_sql_func("SELECT NOW();", "app-789")

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        assert "app-789" in result["result"], "Result should mention app_id"
        print("✅ test_local_query_with_app_id: PASSED")

    def test_local_complex_query(self):
        """Test SQL execution with complex query."""
        sql = """SELECT u.id, u.name, COUNT(o.id) as order_count
FROM users u
LEFT JOIN orders o ON u.id = o.user_id
WHERE u.status = 'active' -- only active users
GROUP BY u.id, u.name
HAVING COUNT(o.id) > 0;"""

        sql = '''-- 创建设备表
CREATE TABLE IF NOT EXISTS devices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  location VARCHAR(255) NOT NULL,
  device_code VARCHAR(100) UNIQUE NOT NULL,
  status VARCHAR(50) DEFAULT 'online',
  last_online_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 创建报警规则表
CREATE TABLE IF NOT EXISTS alert_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  description TEXT,
  pest_count_threshold INTEGER,
  temperature_min DECIMAL(5,2),
  temperature_max DECIMAL(5,2),
  humidity_min DECIMAL(5,2),
  humidity_max DECIMAL(5,2),
  is_enabled BOOLEAN DEFAULT true,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 创建监测数据表
CREATE TABLE IF NOT EXISTS monitoring_data (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id UUID REFERENCES devices(id) ON DELETE CASCADE,
  pest_count INTEGER NOT NULL DEFAULT 0,
  temperature DECIMAL(5,2),
  humidity DECIMAL(5,2),
  image_url TEXT,
  recorded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 创建报警记录表
CREATE TABLE IF NOT EXISTS alert_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id UUID REFERENCES devices(id) ON DELETE CASCADE,
  rule_id UUID REFERENCES alert_rules(id) ON DELETE SET NULL,
  alert_type VARCHAR(100) NOT NULL,
  alert_level VARCHAR(50) DEFAULT 'warning',
  message TEXT NOT NULL,
  pest_count INTEGER,
  temperature DECIMAL(5,2),
  humidity DECIMAL(5,2),
  status VARCHAR(50) DEFAULT 'pending',
  handled_by VARCHAR(255),
  handled_at TIMESTAMP WITH TIME ZONE,
  notes TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 创建通知记录表
CREATE TABLE IF NOT EXISTS notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  alert_id UUID REFERENCES alert_records(id) ON DELETE CASCADE,
  channel VARCHAR(50) NOT NULL,
  recipient VARCHAR(255) NOT NULL,
  status VARCHAR(50) DEFAULT 'pending',
  sent_at TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 创建索引以提高查询性能
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_monitoring_data_device_id ON monitoring_data(device_id);
CREATE INDEX IF NOT EXISTS idx_monitoring_data_recorded_at ON monitoring_data(recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_records_device_id ON alert_records(device_id);
CREATE INDEX IF NOT EXISTS idx_alert_records_status ON alert_records(status);
CREATE INDEX IF NOT EXISTS idx_alert_records_created_at ON alert_records(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_alert_id ON notifications(alert_id);

-- 插入示例数据
INSERT INTO devices (name, location, device_code, status) VALUES
  ('摄像头-001', '仓库A区', 'CAM-001', 'online'),
  ('摄像头-002', '仓库B区', 'CAM-002', 'online'),
  ('摄像头-003', '仓库C区', 'CAM-003', 'offline'),
  ('摄像头-004', '仓库D区', 'CAM-004', 'online');

INSERT INTO alert_rules (name, description, pest_count_threshold, temperature_min, temperature_max, humidity_min, humidity_max, is_enabled) VALUES
  ('高虫害数量预警', '当虫害数量超过50只时触发', 50, NULL, NULL, NULL, NULL, true),
  ('温度异常预警', '温度超出正常范围时触发', NULL, 15.0, 30.0, NULL, NULL, true),
  ('湿度异常预警', '湿度超出正常范围时触发', NULL, NULL, NULL, 40.0, 70.0, true),
  ('综合环境预警', '综合监测多个指标', 30, 18.0, 28.0, 45.0, 65.0, true);

INSERT INTO monitoring_data (device_id, pest_count, temperature, humidity, recorded_at) 
SELECT 
  d.id,
  floor(random() * 100)::INTEGER,
  round((random() * 15 + 15)::numeric, 2),
  round((random() * 30 + 40)::numeric, 2),
  NOW() - (interval '1 hour' * floor(random() * 72))
FROM devices d, generate_series(1, 20);

INSERT INTO alert_records (device_id, rule_id, alert_type, alert_level, message, pest_count, temperature, humidity, status)
SELECT 
  d.id,
  r.id,
  CASE 
    WHEN random() < 0.5 THEN '虫害数量超标'
    ELSE '环境参数异常'
  END,
  CASE 
    WHEN random() < 0.3 THEN 'critical'
    WHEN random() < 0.6 THEN 'warning'
    ELSE 'info'
  END,
  '检测到异常情况，请及时处理',
  floor(random() * 100)::INTEGER,
  round((random() * 15 + 15)::numeric, 2),
  round((random() * 30 + 40)::numeric, 2),
  CASE 
    WHEN random() < 0.4 THEN 'pending'
    WHEN random() < 0.7 THEN 'processing'
    ELSE 'resolved'
  END
FROM devices d
CROSS JOIN alert_rules r
WHERE random() < 0.3
LIMIT 15;
'''

        result = supabase_sql_func(sql)

        assert result["status"] == "success", f"Expected success, got {result['status']}"
        print("✅ test_local_complex_query: PASSED")

    async def test_k8s_simple_query(self):
        """Test K8S execution for simple query."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_supabase_sql",
            parameters={"query": "SELECT NOW();"},
            test_name="supabase-sql-simple"
        )

        self.assert_success(result, "test_k8s_simple_query")

    async def test_k8s_query_with_app_id(self):
        """Test K8S execution with app_id."""
        result = await self.execute_tool_in_k8s(
            tool_name="miaoda_supabase_sql",
            parameters={"query": "SELECT NOW();", "app_id": "app-999"},
            test_name="supabase-sql-appid"
        )

        self.assert_success(result, "test_k8s_query_with_app_id")


async def run_all_tests_async():
    """Run all Supabase tools tests (async version)."""
    print("=" * 80)
    print("SUPABASE TOOLS TEST SUITE")
    print("=" * 80)

    # Test supabase_init
    print("\n" + "=" * 80)
    print("[SUPABASE INIT TESTS]")
    print("=" * 80)
    init_test = TestSupabaseInit()
    print("\n[Local Function Tests]")
    init_test.test_local_simple_init()
    init_test.test_local_init_with_app_id()
    init_test.test_local_init_special_chars()
    print("\n[K8S Pod Execution Tests]")
    await init_test.test_k8s_simple_init()
    await asyncio.sleep(0.3)  # Give time for cleanup between tests
    await init_test.test_k8s_init_with_app_id()
    await asyncio.sleep(0.5)  # Give extra time after tests

    # Test supabase_migration
    print("\n" + "=" * 80)
    print("[SUPABASE MIGRATION TESTS]")
    print("=" * 80)
    migration_test = TestSupabaseMigration()
    print("\n[Local Function Tests]")
    migration_test.test_local_simple_migration()
    migration_test.test_local_complex_migration()
    migration_test.test_local_migration_with_special_chars()
    print("\n[K8S Pod Execution Tests]")
    await migration_test.test_k8s_simple_migration()
    await asyncio.sleep(0.3)  # Give time for cleanup between tests
    await migration_test.test_k8s_complex_migration()
    await asyncio.sleep(0.5)  # Give extra time after tests

    # Test supabase_sql_execution
    print("\n" + "=" * 80)
    print("[SUPABASE SQL EXECUTION TESTS]")
    print("=" * 80)
    sql_test = TestSupabaseSqlExecution()
    print("\n[Local Function Tests]")
    sql_test.test_local_simple_query()
    sql_test.test_local_query_with_app_id()
    sql_test.test_local_complex_query()
    print("\n[K8S Pod Execution Tests]")
    await sql_test.test_k8s_simple_query()
    await asyncio.sleep(0.3)  # Give time for cleanup between tests
    await sql_test.test_k8s_query_with_app_id()
    await asyncio.sleep(0.5)  # Give extra time after tests

    print("\n" + "=" * 80)
    print("✅ ALL SUPABASE TOOLS TESTS PASSED")
    print("=" * 80)


def run_all_tests():
    """Run all Supabase tools tests (sync wrapper)."""
    import warnings
    warnings.filterwarnings('ignore', category=ResourceWarning)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_all_tests_async())
        # Give time for all async cleanup to complete
        loop.run_until_complete(asyncio.sleep(1.0))
    finally:
        # Close any remaining tasks
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    try:
        run_all_tests()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
