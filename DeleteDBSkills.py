from langgraph.store.postgres import PostgresStore
import os, psycopg

conn = psycopg.connect(os.environ["POSTGRES_URI"], autocommit=True)
store = PostgresStore(conn)
store.setup()
store.delete(("user_profile",), "skills")
print("履歷清除完成")