# menu.py
# --------
# This file contains all functions that interact with the user.
# It prints menus, receives input, and uses other modules.

from process import list_processes, kill_process
from systeminfo import get_cpu_usage, get_memory_usage

def show_menu():
    """
    Prints the main menu.
    """
    print("\n=== Simple Task Manager ===")
    print("1. Show running processes")
    print("2. Kill a process")
    print("3. Show system information")
    print("4. Exit")


def menu_loop():
    """
    The main menu loop that keeps the program running.
    """
    while True:
        show_menu()
        choice = input("Enter choice: ")

        if choice == "1":
            processes = list_processes()
            print("\n=== Running Processes ===")
            for p in processes:
                print(f"PID: {p['pid']}  |  Name: {p['name']}")

        elif choice == "2":
            pid = int(input("Enter PID to kill: "))
            if kill_process(pid):
                print("Process killed successfully.")
            else:
                print("Failed to kill process.")

        elif choice == "3":
            cpu = get_cpu_usage()
            mem = get_memory_usage()

            print("\n=== System Information ===")
            print(f"CPU Usage: {cpu}%")
            print(f"Memory Used: {mem['percent']}%")
            print(f"Total RAM: {mem['total'] // (1024**2)} MB")
            print(f"Used RAM:  {mem['used'] // (1024**2)} MB")
            print(f"Free RAM:  {mem['free'] // (1024**2)} MB")

        elif choice == "4":
            print("Exiting...")
            break

        else:
            print("Invalid choice, try again.")