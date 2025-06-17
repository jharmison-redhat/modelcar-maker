def normalize(repo_id: str) -> str:
    return repo_id.replace("/", "--").replace(".", "_").lower()


class Truthy(str):
    """Casting to boolean returns True when set to "true", "yes", "1", or any case of the above."""

    def __bool__(self) -> bool:
        match self.lower():
            case "true":
                return True
            case "yes":
                return True
            case "1":
                return True
            case _:
                return False
