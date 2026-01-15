from abc import ABC, abstractmethod

class BrokerAdapter(ABC):
    @abstractmethod
    def place_trade(self, user, proposal) -> tuple[bool, str]:
        """Return (success, message)."""
        raise NotImplementedError
