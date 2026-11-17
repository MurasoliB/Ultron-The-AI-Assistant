from memory import remember_name, get_name
from commands import check_name_command, check_memory_command

print("Current name:", get_name())

print(check_name_command("my name is MURASOLI"))

print(check_memory_command("what is my name"))

print("Saved name:", get_name())