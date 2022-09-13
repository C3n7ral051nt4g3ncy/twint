import twint
import os

'''
Test.py - Testing TWINT to make sure everything works.
'''


def test_reg(c, run):
    print(f"[+] Beginning vanilla test in {str(run)}")
    run(c)


def test_db(c, run):
    print(f"[+] Beginning DB test in {str(run)}")
    c.Database = "test_twint.db"
    run(c)


def custom(c, run, _type):
    print(f"[+] Beginning custom {_type} test in {str(run)}")
    c.Custom['tweet'] = ["id", "username"]
    c.Custom['user'] = ["id", "username"]
    run(c)


def test_json(c, run):
    c.Store_json = True
    c.Output = "test_twint.json"
    custom(c, run, "JSON")
    print(f"[+] Beginning JSON test in {str(run)}")
    run(c)


def test_csv(c, run):
    c.Store_csv = True
    c.Output = "test_twint.csv"
    custom(c, run, "CSV")
    print(f"[+] Beginning CSV test in {str(run)}")
    run(c)


def main():
    c = twint.Config()
    c.Username = "verified"
    c.Limit = 20
    c.Store_object = True

    # Separate objects are necessary.

    f = twint.Config()
    f.Username = "verified"
    f.Limit = 20
    f.Store_object = True
    f.User_full = True

    runs = [
        twint.run.Profile,  # this doesn't
        twint.run.Search,  # this works
        twint.run.Following,
        twint.run.Followers,
        twint.run.Favorites,
    ]

    tests = [test_reg, test_json, test_csv, test_db]

    # Something breaks if we don't split these up

    for run in runs[:3]:
        if run == twint.run.Search:
            c.Since = "2012-1-1 20:30:22"
            c.Until = "2017-1-1"
        else:
            c.Since = ""
            c.Until = ""

        for test in tests:
            test(c, run)

    for run in runs[3:]:
        for test in tests:
            test(f, run)

    files = ["test_twint.db", "test_twint.json", "test_twint.csv"]
    for _file in files:
        os.remove(_file)

    print("[+] Testing complete!")


if __name__ == '__main__':
    main()
