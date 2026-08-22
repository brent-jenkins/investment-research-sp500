set -e
python step5_run_arm.py pool1 a_none 3.0 unlimited none
for p in pool2_clean pool1; do
  python step5_run_arm.py $p b30  3.0 unlimited 0.30
  python step5_run_arm.py $p b50  3.0 unlimited 0.50
  python step5_run_arm.py $p b100 3.0 unlimited 1.00
done
echo "ALL_ARMS_DONE"
