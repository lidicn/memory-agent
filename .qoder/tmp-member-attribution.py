import sqlite3

c = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)


def q(sql):
    try:
        return c.execute(sql).fetchall()
    except Exception as exc:
        return [("ERR", type(exc).__name__, str(exc)[:80])]


print("members =", q("select count(*) from members")[0][0])
print("behavior_states_total =", q("select count(*) from behavior_states")[0][0])
print("behavior_states_with_member =",
      q("select count(*) from behavior_states where coalesce(member,'')<>''")[0][0])
print("behavior_states_days =",
      q("select count(distinct substr(ts,1,10)) from behavior_states")[0][0])
print("behavior_events_with_persons =",
      q("select count(*) from behavior_events where coalesce(persons,'') not in ('','[]')")[0][0])
print("events_with_person =", q("select count(*) from events where coalesce(person,'')<>''")[0][0])
print("top_state_days =", [(r[0], r[1]) for r in
      q("select substr(ts,1,10) d, count(*) from behavior_states group by d order by d desc limit 5")])
