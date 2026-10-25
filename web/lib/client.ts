import { DbConnection } from "./module_bindings";
import { RoomSession } from "./state";
export function connect(url: string, database: string) {
  const key = `tandem:participant:${url}:${database}`;
  let session: RoomSession;
  let builder = DbConnection.builder()
    .withUri(url)
    .withDatabaseName(database)
    .withAutomaticReconnect()
    .onConnect((_connection, _identity, token) => {
      localStorage.setItem(key, token);
      session.connected();
    })
    .onAutomaticReconnect(() => session.connected())
    .onDisconnect(() => session.disconnected())
    .onConnectError(() =>
      session.error("Could not connect. Check the database URL and name."),
    );
  const token = localStorage.getItem(key);
  if (token) builder = builder.withToken(token);
  const connection = builder.build();
  session = new RoomSession(connection);
  return {
    connection,
    session,
    close: () => {
      session.dispose();
      connection.disconnect();
    },
  };
}
