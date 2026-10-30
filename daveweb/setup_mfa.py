#!/usr/bin/env python3
"""Run this once to generate your login credentials.

    python3 setup_mfa.py

It will:
  1. Ask for the password you want to log in with, hash it (never stores
     the plaintext), and print the hash for your .env.
  2. Generate a random TOTP secret, print it for your .env, and save a QR
     code (mfa_qr.png) you scan with your authenticator app (Google
     Authenticator, Authy, 1Password, whatever you use).

You paste the two printed env vars into daveweb's .env, then delete
mfa_qr.png once it's scanned -- it's a plain-text-equivalent secret sitting
on disk otherwise.
"""
import getpass
import secrets

import pyotp
import qrcode
from werkzeug.security import generate_password_hash


def main() -> None:
    print("=== D.A.V.E. Web -- credential setup ===\n")

    password = getpass.getpass("Choose a login password: ")
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords didn't match. Run it again.")
        return
    if len(password) < 12:
        print("That's a short password for something sitting on the open "
              "internet behind Funnel. Use a real passphrase, not a vibe.")
        return

    pw_hash = generate_password_hash(password)

    totp_secret = pyotp.random_base32()
    totp = pyotp.TOTP(totp_secret)
    issuer = "DAVE"
    account = input("Account label for your authenticator app [youruser@yourhost]: ").strip() or "youruser@yourhost"
    uri = totp.provisioning_uri(name=account, issuer_name=issuer)

    img = qrcode.make(uri)
    img.save("mfa_qr.png")

    flask_secret = secrets.token_hex(32)

    print("\n--- Add these to daveweb/.env ---\n")
    print(f"DAVEWEB_PASSWORD_HASH={pw_hash}")
    print(f"DAVEWEB_TOTP_SECRET={totp_secret}")
    print(f"DAVEWEB_SECRET_KEY={flask_secret}")
    print("\nScan mfa_qr.png with your authenticator app now, then delete "
          "the file (it's a bare, unencrypted copy of your TOTP secret):")
    print("  rm mfa_qr.png")
    print("\nIf scanning fails, you can enter the secret manually instead: "
          f"{totp_secret}")


if __name__ == "__main__":
    main()
