import asyncio
import unittest
import sys
from unittest.mock import AsyncMock, patch, MagicMock

# Mock oracledb module
mock_oracledb = MagicMock()
sys.modules["oracledb"] = mock_oracledb

from app.services.db_import_service import DBImportService

class TestOracleImport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = {
            "type": "oracle", "host": "localhost", "port": 1521,
            "user": "test_user", "password": "test_password", "database": "xe"
        }
        # Reset connect_async mock
        mock_oracledb.connect_async = AsyncMock()

    async def test_test_oracle_connection(self):
        mock_conn = AsyncMock()
        mock_oracledb.connect_async.return_value = mock_conn
        
        result = await DBImportService.test_oracle_connection(self.config)
        self.assertTrue(result)
        mock_oracledb.connect_async.assert_called_once()
        mock_conn.close.assert_called_once()

    async def test_get_oracle_tables(self):
        mock_conn = MagicMock() # Use MagicMock for connection to control method types better
        mock_conn.connect_async = AsyncMock() # Not used here but good to have
        mock_conn.close = AsyncMock()
        
        mock_cur = AsyncMock()
        # Mocking the async context manager: async with conn.cursor() as cur
        mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__aexit__ = AsyncMock()
        
        mock_oracledb.connect_async.return_value = mock_conn
        mock_cur.fetchall.return_value = [("TABLE1", "Comment 1"), ("TABLE2", None)]
        
        tables = await DBImportService.get_oracle_tables(self.config)
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0]["name"], "TABLE1")

    async def test_get_oracle_ddl(self):
        mock_conn = MagicMock()
        mock_conn.close = AsyncMock()
        
        mock_cur = AsyncMock()
        mock_conn.cursor.return_value.__aenter__ = AsyncMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__aexit__ = AsyncMock()
        
        mock_oracledb.connect_async.return_value = mock_conn
        mock_cur.fetchone.return_value = ["CREATE TABLE TABLE1 ..."]
        
        ddl = await DBImportService.get_oracle_ddl(self.config, ["TABLE1"])
        self.assertIn("CREATE TABLE TABLE1", ddl)

if __name__ == "__main__":
    unittest.main()