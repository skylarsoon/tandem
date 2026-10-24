// Dev/test helper: connects as STDB_TOKEN's identity (like an open browser tab)
// and stays connected until killed. Used by the presence tests and demos.

import { DbConnection } from '../module_bindings';

const STDB_URL = process.env.STDB_URL ?? 'http://127.0.0.1:3000';
const STDB_DB = process.env.STDB_DB ?? 'tandem-dev';

DbConnection.builder()
  .withUri(STDB_URL)
  .withDatabaseName(STDB_DB)
  .withToken(process.env.STDB_TOKEN)
  .onConnect((_conn, identity) => console.log(`connected 0x${identity.toHexString()}`))
  .onConnectError((_ctx, err) => {
    console.error(`connection error: ${err.message}`);
    process.exit(1);
  })
  .build();
