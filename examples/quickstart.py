from tenkyu import Guard, Signup

guard = Guard()  # in memory, so a restart forgets everyone. Reuse one Guard per process.
# In your app, keep the history on disk instead: Guard(store=SQLiteStore("tenkyu.db"))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"

first = guard.check(Signup(email="john@gmail.com", ip="203.0.113.7", device_id="dev-1", user_agent=UA))
print(first.action, first.reasons)  # allow []

# Same person, new alias, hoping for another batch of free credits:
again = guard.check(Signup(email="j.o.h.n+free2@gmail.com", ip="203.0.113.7", device_id="dev-1", user_agent=UA))
print(again.action, again.reasons)  # block ['this email (or an alias of it) already signed up']

if again.action != "allow":
    print("withhold free credits; send to manual review" if again.action == "review" else "reject signup")
