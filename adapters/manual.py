from adapters.base import BrokerAdapter

class ManualAdapter(BrokerAdapter):
    def place_trade(self, user, proposal):
        # Manual execution for now (user executes in TopstepX)
        return True, "Manual mode: execute this trade in TopstepX now."
