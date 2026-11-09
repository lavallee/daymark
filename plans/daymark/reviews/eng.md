# Engineering review

Primary risks and required verification: caller transaction rollback includes event
and operation journal; concurrent lease claims are exclusive; operation replay
cannot spawn recurrence twice; completion cancels pending reminders; workspace refs
cannot cross boundaries; restore is application-owned and verified before activation.
Tests must exercise these boundaries, not mirror implementation. No fake human gate.
