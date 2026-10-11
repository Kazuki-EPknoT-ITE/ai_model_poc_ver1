"""PyInstaller の入口（T-19）。配布先の PC には Python が無いので office-tools を1フォルダにまとめる。"""

from office_tools.server import main

if __name__ == "__main__":
    main()
