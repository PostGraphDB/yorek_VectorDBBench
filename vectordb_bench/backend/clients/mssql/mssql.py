"""Wrapper around MSSQL"""

import logging
from contextlib import contextmanager
from typing import Any, Generator, Optional, Tuple, Sequence

from ..api import VectorDB, DBCaseConfig

import pyodbc
import struct

log = logging.getLogger(__name__) 

class MSSQL(VectorDB):    
    def __init__(
        self,
        dim: int,
        db_config: dict,
        db_case_config: DBCaseConfig,
        collection_name: str = "vector",
        drop_old: bool = False,
        **kwargs,
    ):
        self.db_config = db_config
        self.case_config = db_case_config
        self.table_name = collection_name + "_" + str(dim)
        self.dim = dim
        self.schema_name = "benchmark"

        log.info("db_case_config: " + str(db_case_config))

        log.info(f"Connecting to MSSQL...")
        #log.info(self.db_config['connection_string'])
        cnxn = pyodbc.connect(self.db_config['connection_string'])     
        cursor = cnxn.cursor()

        log.info(f"Creating schema...")
        cursor.execute(f""" 
            if (schema_id('{self.schema_name}') is null) begin
                exec('create schema [{self.schema_name}] authorization [dbo];')
            end;
        """)
        cnxn.commit()
        
        if drop_old:
            log.info(f"Dropping existing table...")
            cursor.execute(f""" 
                drop table if exists [{self.schema_name}].[{self.table_name}]
            """)           
            cnxn.commit()

        log.info(f"Creating vector table...")
        create_table = f""" 
            if object_id('[{self.schema_name}].[{self.table_name}]') is null begin
                create table [{self.schema_name}].[{self.table_name}] (
                    id int not null primary key clustered,
                    [vector] vector({self.dim}) not null
                )                
            end
        """
        log.info(f"vector table schema: {create_table}")   
        cursor.execute(create_table)

        cnxn.commit()
            
        log.info(f"Creating table type...")
        cursor.execute(f""" 
	      drop procedure if exists stp_load_vectors
              drop type if exists dbo.vector_payload
                create type dbo.vector_payload as table
                (
                    id int not null,
                    [vector] vector({self.dim}) not null
                )
        """)
        cursor.commit()

        log.info(f"Creating stored procedure...")
        cursor.execute(f""" 
            create or alter procedure dbo.stp_load_vectors
            @dummy int,
            @payload dbo.vector_payload readonly
            as
            begin
                set nocount on
                insert into [{self.schema_name}].[{self.table_name}] (id, vector) select id, cast(cast([vector] as nvarchar(max)) as vector({self.dim})) from @payload;
            end
        """)
        cnxn.commit()

        cursor.close()
        cnxn.close()
            
    @contextmanager
    def init(self) -> Generator[None, None, None]:
        cnxn = pyodbc.connect(self.db_config['connection_string'])     
        self.cnxn = cnxn    
        cnxn.autocommit = True
        self.cursor = cnxn.cursor()
        yield 
        self.cursor.close()
        self.cnxn.close()

    def ready_to_load(self):
        log.info(f"MSSQL ready to load")
        pass

    def optimize(self):
        log.info(f"MSSQL optimize")
        pass

    def ready_to_search(self):
        log.info(f"MSSQL ready to search")
        pass

    def array_to_vector(self, a:list[float]) -> str:
        # TODO, for now, create a string representation of the vector
        # Cast that to VARCHAR and then to Vector, this is temporary
        res = '['
        
        for f in a:
            res += str(f) + ', '

        res = res[:-2]
        res += ']'

        return res
        return b
    
    def insert_embeddings(
        self,
        embeddings: list[list[float]],
        metadata: list[int],
        **kwargs: Any,
    ) -> Tuple[int, Optional[Exception]]:   
        try:            
            log.info(f'Loading batch of {len(metadata)} vectors...')
            #return len(metadata), None
        
            log.info(f'Generating param list...')
            params = [(metadata[i], self.array_to_vector(embeddings[i])) for i in range(len(metadata))]

            log.info(f'Loading table...')
            cursor = self.cursor
            #cursor.fast_executemany = True               
            cursor.execute("EXEC dbo.stp_load_vectors @dummy=?, @payload=?", (1, params))     

            return len(metadata), None
        except Exception as e:
            #cursor.rollback()
            log.warning(f"Failed to insert data into vector table ([{self.schema_name}].[{self.table_name}]), error: {e}")   
            return 0, e

    def search_embedding(        
        self,
        query: list[float],
        k: int = 100,
        filters: dict | None = None,
        timeout: int | None = None,
    ) -> list[int]:        
        search_param = self.case_config.search_param()
        metric_function = search_param["metric"]
        #efSearch = search_param["efSearch"]
        #log.info(f'Query top:{k} metric:{metric_fun} filters:{filters} params: {search_param} timeout:{timeout}...')
        cursor = self.cursor
        if filters:
            cursor.execute(f"""            
                select top(?) v.id from [{self.schema_name}].[{self.table_name}] v where v.id >= ? order by vector_distance(cast(? as varchar(20)), cast(cast(? as nvarchar(max)) as vector({self.dim})), v.[vector])
                """, 
                k,                
                int(filters.get('id')),
                metric_function,
                self.array_to_vector(query)
                )
        else:
            cursor.execute(f"""            
                select top(?) v.id from [{self.schema_name}].[{self.table_name}] v order by vector_distance(cast(? as varchar(20)), cast(cast(? as nvarchar(max)) as vector({self.dim})), v.[vector])
                """,
                k,
                metric_function,
                self.array_to_vector(query)
                )
        rows = cursor.fetchall()
        res = [row.id for row in rows]
        return res
        
        
