# Runbook: PRD 5, the switch to the CPU server

PRD 5 PR 4 removes the reader and model containers. Scans, chat and embeddings
go to Gemini through OpenRouter, and RapidOCR runs in the worker. The server
then needs no more than a CPU, so the instance moves from g4dn.xlarge to
t3.xlarge with Unlimited CPU credits (pending Vrushit; an
m7i.xlarge if the surplus-credit charges in the exit test cost more than the
price gap).

**Order: code first, then hardware.** Release PR 4 on the g4dn first (its
graphics card just sits unused), then change the instance type. Rollback is
the other way round: hardware first, then code, because a revert of PR 4
cannot run on a t3.

Tushar runs every AWS command. Run them in AWS CloudShell or Git Bash, with
the AWS CLI signed in to the Kaagaz account. Server commands run through
Session Manager as `ubuntu` (`sudo -iu ubuntu`), in `/home/ubuntu/kaagaz`, with
`dc` as in docs/running-locally.md, Production on EC2:

```
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'
```

Set these once in the AWS shell:

```
export AWS_REGION=ap-south-1
IID=<the Kaagaz instance id, i-...>
EIP=<the Elastic IP>
```

## A. Before release

1. On the server: keep it on, and take a fresh database dump.

   ```
   sudo touch /etc/kaagaz/no-idle-stop
   sudo systemctl start kaagaz-pgdump.service && ls -l ~/backups | tail -n 2
   ```

   Expect today's `kaagaz-YYYYMMDD.dump`, not empty.

2. In the AWS shell: both new types are offered in the instance's zone, and
   the instance has ENA (t3 needs it).

   ```
   aws ec2 describe-instances --instance-ids "$IID" \
     --query 'Reservations[0].Instances[0].[Placement.AvailabilityZone,EnaSupport,InstanceType]' --output text
   aws ec2 describe-instance-type-offerings --location-type availability-zone \
     --filters Name=location,Values=ap-south-1c Name=instance-type,Values=t3.xlarge,m7i.xlarge \
     --query 'InstanceTypeOfferings[].InstanceType' --output text
   ```

   Expect `ap-south-1c True g4dn.xlarge`, then both `t3.xlarge` and
   `m7i.xlarge`. If the zone is not ap-south-1c, use the zone it prints.

3. On the server:

   ```
   cat /etc/fstab
   docker info --format '{{.DockerRootDir}} {{.DefaultRuntime}}'
   swapon --show
   ```

   * `/etc/fstab` has only the root disk and `/swapfile`. A line for the
     instance-store disk (an `nvme` device or a mount under `/opt/dlami`)
     without `nofail` hangs a t3 at boot: add `nofail` to it, or delete it.
   * `docker info` prints `/var/lib/docker runc`. Anything else: stop here and
     ask Vrushit.
   * `swapon --show` lists `/swapfile` only.

4. The `.env` edit, on the server. Back it up first:

   ```
   cd /home/ubuntu/kaagaz
   cp -p .env ~/env-before-pr4
   set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >>.env; fi; }
   set_env OCR_ENGINE gemini
   set_env OCR_DPI 300
   set_env OCR_TIMEOUT_SECONDS 120
   set_env MODEL_ENABLED true
   grep -E '^(OCR_ENGINE|OCR_DPI|OCR_TIMEOUT_SECONDS|MODEL_ENABLED|OCR_MODEL|MODEL_CHAT|MODEL_EMBED|EXTRACT_WINDOW_CHARS|EXTRACT_WINDOW_OVERLAP)=' .env
   grep -c '^OPENROUTER_API_KEY=.' .env
   ```

   * The first `grep` shows the four values above. `OCR_MODEL`, `MODEL_CHAT` and
     `MODEL_EMBED` must each be a `google/gemini-` model (`MODEL_EMBED` is
     `google/gemini-embedding-2`); with any other, the api and worker stop at
     start-up naming it. Copy the line from `.env.example` with `set_env` if
     one is not.
   * `EXTRACT_WINDOW_CHARS` and `EXTRACT_WINDOW_OVERLAP`, if listed, must be
     empty, or `EXTRACT_WINDOW_CHARS` 1700 or less with `EXTRACT_WINDOW_OVERLAP`
     below it. They are now checked against the fixed 2048-token invoice
     reply, and with any other value the api and worker stop at start-up
     naming it. Empty, the windows are 1700 characters with a 400 overlap.
   * The second `grep` prints `1`: the OpenRouter key is set. It never prints
     the key.
   * Until step 5, the old compose files still choose the old reader and
     model address themselves, whatever `.env` says. The deploy timer's next
     run restarts the api and worker with the new `.env`.

## B. Release PR 4 on the g4dn

5. Release PR 4 the usual way (docs/running-locally.md, Releases). Then
   watch the deploys:

   ```
   journalctl -u kaagaz-deploy -f
   ```

   **Expected once: the first run logs `<sha> failed`.** The deploy script
   that is already running still holds the old compose file list, and one of
   those files is gone in the new commit, so Compose stops. The commit is not
   recorded, so the next run, within 5 minutes, uses the new script and logs
   `<sha> deployed`. It also removes the containers the new files no longer
   have (`--remove-orphans`). Two `failed` lines in a row: stop and read
   `journalctl -u kaagaz-deploy -n 100`.

6. Checks, on the server:

   ```
   docker ps -a --format '{{.Names}} {{.Status}}'
   dc run --rm worker python -m kaagaz.cli verify-models
   ```

   * `docker ps -a` lists only `api`, `caddy`, `clamav`, `elasticmq`,
     `frontend`, `migrate` (exited 0), `postgres`, `seaweedfs` and the
     `worker` copies.
   * `verify-models`: every line says PASS.
   * Upload a made-up Marathi scan: it reaches Ready and its pages say
     "Read by Gemini".

   Then delete the settings PR 4 removed. This lists every name in `.env` that
   `.env.example` does not have:

   ```
   comm -23 <(grep -oE '^[A-Z][A-Z0-9_]*' .env | sort -u) <(grep -oE '^[A-Z][A-Z0-9_]*' .env.example | sort -u)
   ```

   It prints the removed settings (the PR description lists all six) and the
   server-only ones such as `KAAGAZ_DOMAIN`, `ACME_EMAIL` and
   `SEAWEEDFS_S3_CONFIG`. Delete only the removed ones, one at a time:

   ```
   sed -i '/^<NAME>=/d' .env
   ```

   A removed setting left in `.env` is ignored, so this is tidying, not a fix.

## C. Switch the instance type

Quiet time; the site is down for about 15 minutes.

7. Wait for empty job queues. The keep-on file makes the dry run stop at its
   first check, so set it aside for that one run (one line, so the idle timer
   has no minute to act on it):

   ```
   sudo mv /etc/kaagaz/no-idle-stop /etc/kaagaz/no-idle-stop.hold; sudo ~/kaagaz/infra/ec2/idle-stop.sh --dry-run; sudo mv /etc/kaagaz/no-idle-stop.hold /etc/kaagaz/no-idle-stop
   ```

   Only `would stop` or `N signed-in API requests` proves both queues are
   empty. Any other `staying on:` answer (a busy job queue, a queue it could
   not read, a running deploy) means wait and run it again.

8. Stop the instance, in the AWS shell:

   ```
   aws ec2 stop-instances --instance-ids "$IID"
   aws ec2 wait instance-stopped --instance-ids "$IID"
   ```

9. The rollback image. It holds the disk and its secrets: never share it or
   copy it out.

   ```
   AMI=$(aws ec2 create-image --instance-id "$IID" --name "kaagaz-rollback-g4dn-$(date -u +%Y%m%d)" \
     --description "Kaagaz before the PRD 5 CPU switch. Holds secrets: never share." --query ImageId --output text)
   echo "$AMI"
   aws ec2 wait image-available --image-ids "$AMI"
   ```

   Write the image id down. If the wait gives up (a big disk takes a while),
   run the wait again.

10. The new type:

    ```
    aws ec2 modify-instance-attribute --instance-id "$IID" --instance-type Value=t3.xlarge
    ```

11. Unlimited CPU credits:

    ```
    aws ec2 modify-instance-credit-specification --instance-credit-specifications "InstanceId=$IID,CpuCredits=unlimited"
    aws ec2 describe-instance-credit-specifications --instance-ids "$IID" --output text
    ```

    Expect `unlimited`.

12. Start it:

    ```
    aws ec2 start-instances --instance-ids "$IID"
    aws ec2 wait instance-running --instance-ids "$IID"
    aws ec2 describe-instances --instance-ids "$IID" \
      --query 'Reservations[0].Instances[0].[InstanceType,State.Name]' --output text
    ```

    Expect `t3.xlarge running`.

13. Update the Wake Lambda. `kaagaz-wake` is set up by hand, so a deploy never
    changes it: give it `infra/aws/wake/handler.py` as it is on `production`
    now (PR 4 changed its message). First check which file and function it
    runs:

    ```
    aws lambda get-function-configuration --function-name kaagaz-wake --query Handler --output text
    ```

    Expect `handler.handler` (the file `handler.py`, its function `handler`).
    Then either paste the file over that file in the Lambda console's code
    editor and choose Deploy, or upload it to AWS CloudShell (Actions, Upload
    file) and run:

    ```
    zip wake.zip handler.py
    aws lambda update-function-code --function-name kaagaz-wake --zip-file fileb://wake.zip
    aws lambda wait function-updated --function-name kaagaz-wake
    ```

    If the Handler names another file, use the console way, in that file, and
    set the Handler to `<that file, without .py>.handler`. The settings
    (`INSTANCE_ID`, `WAKE_TOKEN`) are not in the code and stay as they are.
    Then open the Wake link: with the server running it says "Already
    running."

14. The Elastic IP stays on the instance through a stop and start:

    ```
    aws ec2 describe-addresses --public-ips "$EIP" --query 'Addresses[0].InstanceId' --output text
    ```

    Expect the same `$IID`.

15. On the server:

    ```
    nproc
    free -g
    systemctl --failed
    dc ps -a
    ```

    * `nproc` is 4, and `free -g` shows about 15 GB of memory.
    * `systemctl --failed`: a failed `nvidia-*` unit is left over from the
      g4dn; turn each one off with `sudo systemctl disable --now <unit>`. Any
      other failed unit: stop and ask Vrushit.
    * `dc ps -a`: every service is running or healthy, `migrate` exited (0).
    * Go-live checks 10.1, 10.3, 10.6, 10.8 and 10.9
      (docs/running-locally.md, Production on EC2).
    * Let the idle stop work again: `sudo rm /etc/kaagaz/no-idle-stop`.
    * After the first busy hours, in the AWS shell, record CPU use and the
      surplus-credit charges in docs/architecture.md, Decisions (the t3.xlarge
      row):

      ```
      for metric in CPUUtilization CPUSurplusCreditsCharged; do
        aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name "$metric" \
          --dimensions Name=InstanceId,Value="$IID" --period 3600 --statistics Average Maximum Sum \
          --start-time "$(date -u -d '-24 hours' +%Y-%m-%dT%H:%M:%SZ)" --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
          --query 'Datapoints[].[Timestamp,Average,Maximum,Sum]' --output text | sort
      done
      ```

16. Lower the Budgets and alarm thresholds to the new monthly cost (the
    amount is Vrushit's call):

    ```
    ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
    aws budgets describe-budgets --account-id "$ACCOUNT" \
      --query 'Budgets[].[BudgetName,BudgetLimit.Amount,BudgetLimit.Unit]' --output text
    aws cloudwatch describe-alarms --region us-east-1 \
      --query 'MetricAlarms[].[AlarmName,MetricName,Threshold]' --output text
    ```

    Change each amount in the Billing console (Budgets, Edit) and each
    threshold in the CloudWatch console (Alarms, Edit), so no other field of
    theirs changes.

## D. Cleanup, 7 days later

17. On the server, remove the old images and the old model store volume. Volumes
    are never pruned in bulk.

    ```
    docker image prune -a -f
    docker builder prune -f
    docker volume ls --filter dangling=true
    ```

    The last command lists the volumes no container uses: expect one, the old
    model store (its name ends in `-data` and is none of `postgres-data`,
    `seaweedfs-data`, `elasticmq-data`, `clamav-data`, `caddy-data` or
    `caddy-config`). Remove only that one, then check the space:

    ```
    docker volume rm <its name>
    df -h /
    ```

18. In the AWS shell, deregister the rollback image and delete its snapshots:

    ```
    SNAPS=$(aws ec2 describe-images --image-ids "$AMI" --query 'Images[0].BlockDeviceMappings[].Ebs.SnapshotId' --output text)
    aws ec2 deregister-image --image-id "$AMI"
    for snap in $SNAPS; do aws ec2 delete-snapshot --snapshot-id "$snap"; done
    ```

19. On the server: `rm ~/env-before-pr4`. Update the offline copy of `.env` in
    the password manager.

## Rollback

* **Gemini problems** (any instance type): set `OCR_ENGINE=rapidocr` in `.env`
  (`set_env OCR_ENGINE rapidocr`, step 4), then `dc up -d api worker`. Scans
  are read by RapidOCR until it is set back. While it is in effect no checker
  reads their values again, so every scanned value shows needs review.
* **Hardware problems:** stop the instance (step 8), set
  `--instance-type Value=g4dn.xlarge` (step 10), and start it (step 12). PR 4
  runs on the g4dn too. Or launch a new instance from the step 9 image with
  `aws ec2 run-instances --image-id "$AMI" --instance-type g4dn.xlarge` and the
  old instance's subnet, security group and instance profile, then move the
  Elastic IP to it by its allocation id:

  ```
  ALLOC=$(aws ec2 describe-addresses --public-ips "$EIP" --query 'Addresses[0].AllocationId' --output text)
  aws ec2 associate-address --allocation-id "$ALLOC" --instance-id <new id>
  ```

  A new instance has a new id: update the Wake Lambda's `INSTANCE_ID` and the
  weekday schedule's target.
* **Back to the old reader stack:** first back to the g4dn (above), then
  revert PR 4 through a pull request into `production`, and restore the old
  `.env` with `cp -p ~/env-before-pr4 .env`. The first deploy after the revert
  starts the stack without the old reader containers; the next run, within 5
  minutes, starts them. After step 17, the old images and model store are
  gone, so the first deploy rebuilds them and the models must be fetched again.

## Known risks

* **Disk:** tens of GB come back only at step 17.
* **Old reads:** pages read before PR 4 say "Read by OCR" (an unknown reader);
  Read again reads them with Gemini.
* **Old runs:** `processing_runs` keeps the old reader names as history; the
  time estimate uses its default for an unknown reader.
* **Old vectors:** vectors with the old recipe tag are embedded again by the
  worker's missing-vector pass (PRD 5 M5), or at once with
  `dc exec api python -m kaagaz.cli reembed`.
* **CPU credits:** Standard credits would slow the workers once spent, so the
  instance runs Unlimited; watch CPUSurplusCreditsCharged (step 15).
* **The machine image:** the disk was set up on a Deep Learning AMI for the
  g4dn, untested on a CPU instance. Step 9's image is the safety net.
