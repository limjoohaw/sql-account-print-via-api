"""CLI tool for first-run admin account setup.

Usage:
    python manage.py create-admin
"""

import sys
import getpass


def create_admin():
    """Interactive prompt to create the initial admin account."""
    # Import here to avoid loading the full app just for CLI
    from auth import create_user, find_user

    print("=== Create Admin Account ===\n")

    username = input("Admin username: ").strip()
    if not username:
        print("Error: Username cannot be empty.")
        sys.exit(1)

    existing = find_user(username)
    if existing:
        print(f"Error: User '{username}' already exists.")
        sys.exit(1)

    password = getpass.getpass("Admin password: ")
    if len(password) < 8:
        print("Error: Password must be at least 8 characters.")
        sys.exit(1)

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: Passwords do not match.")
        sys.exit(1)

    user = create_user(
        username=username,
        password=password,
        companies=[],  # Admin can assign companies to themselves via the admin panel
        is_admin=True,
    )
    print(f"\nAdmin account '{user.username}' created successfully.")
    print("You can now start the app and log in via the browser.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python manage.py <command>")
        print("\nCommands:")
        print("  create-admin    Create the initial admin account")
        sys.exit(1)

    command = sys.argv[1]
    if command == "create-admin":
        create_admin()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
